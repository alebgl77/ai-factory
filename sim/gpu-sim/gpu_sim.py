# SPDX-License-Identifier: MIT
"""DCGM-compatible GPU telemetry simulator for the key-less demo.

Serves Prometheus text exposition on :9400/metrics with the metric names and labels of
the NVIDIA dcgm-exporter (DCGM_FI_DEV_GPU_UTIL, DCGM_FI_DEV_GPU_TEMP,
DCGM_FI_DEV_POWER_USAGE, DCGM_FI_DEV_FB_USED, DCGM_FI_DEV_FB_FREE, ...), so the same
Prometheus rules, Grafana dashboards and API code work with the simulator and with a
real local GPU.

The utilisation follows the traffic served by the "local" profile of sim/mock-llm (the
stand-in for the local GPU inference server): the simulator polls GET /stats and turns
the generated tokens per second and the in-flight requests into a utilisation, with
thermal and power inertia. When mock-llm is unreachable it falls back to a smooth
synthetic daily pattern.

Standard library only (no third-party dependency).
"""

from __future__ import annotations

import json
import math
import os
import random
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PORT = _env_int("GPU_SIM_PORT", 9400)
COUNT = max(1, min(8, _env_int("GPU_SIM_COUNT", 1)))
MODEL_NAME = os.environ.get("GPU_SIM_MODEL", "NVIDIA RTX A5000")
HOSTNAME = os.environ.get("GPU_SIM_HOSTNAME", "gpu-local-01")
DRIVER = os.environ.get("GPU_SIM_DRIVER_VERSION", "580.95.05")
MEM_TOTAL_MIB = _env_int("GPU_SIM_MEM_TOTAL_MIB", 24564)
MODEL_WEIGHTS_MIB = _env_int("GPU_SIM_MODEL_WEIGHTS_MIB", 9800)
TDP_W = _env_float("GPU_SIM_TDP_W", 230.0)
IDLE_W = _env_float("GPU_SIM_IDLE_W", 22.0)
AMBIENT_C = _env_float("GPU_SIM_AMBIENT_C", 31.0)
# Extra degrees at full load; with the defaults the GPU peaks around 77 C (alert at 83 C).
HEAT_SPAN_C = _env_float("GPU_SIM_HEAT_SPAN_C", 46.0)
CAPACITY_TPS = _env_float("GPU_SIM_CAPACITY_TPS", 90.0)
SOURCE_URL = os.environ.get("GPU_SIM_SOURCE_URL", "http://mock-llm:8080/stats")
SOURCE_PROFILE = os.environ.get("GPU_SIM_SOURCE_PROFILE", "local")
TICK_S = max(1.0, _env_float("GPU_SIM_TICK_S", 5.0))


class Gpu:
    def __init__(self, index: int):
        self.index = index
        rnd = random.Random(f"{HOSTNAME}-{index}")
        self.uuid = "GPU-" + "-".join(
            "".join(rnd.choice("0123456789abcdef") for _ in range(n)) for n in (8, 4, 4, 4, 12))
        self.pci_bus_id = f"00000000:{index + 1:02X}:00.0"
        # The second and further GPUs take a smaller share of the load.
        self.share = 1.0 if index == 0 else 0.35
        self.util = 3.0
        self.temp = AMBIENT_C + 4.0
        self.mem_temp = self.temp + 4.0
        self.power = IDLE_W
        self.energy_mj = 0.0
        self.kv_mib = 0.0
        self.mem_copy_util = 0.0

    def step(self, load: float, in_flight: float, dt: float) -> None:
        target = min(99.0, max(0.0, 2.0 + 100.0 * load * self.share + random.gauss(0, 1.5)))
        # Utilisation reacts fast, temperature and memory slowly.
        self.util += (target - self.util) * min(1.0, dt / 8.0)
        self.util = max(0.0, min(100.0, self.util))
        temp_target = AMBIENT_C + 3.0 + HEAT_SPAN_C * (self.util / 100.0) ** 0.8
        self.temp += (temp_target - self.temp) * min(1.0, dt / 60.0)
        self.mem_temp = self.temp + 4.0 + self.util * 0.04
        self.power = IDLE_W + (TDP_W - IDLE_W) * (self.util / 100.0) + random.gauss(0, 2.0)
        self.power = max(IDLE_W * 0.8, min(TDP_W * 1.02, self.power))
        self.energy_mj += self.power * dt * 1000.0
        kv_target = min(MEM_TOTAL_MIB - MODEL_WEIGHTS_MIB - 600,
                        in_flight * self.share * 420.0 + 300.0)
        self.kv_mib += (kv_target - self.kv_mib) * min(1.0, dt / 20.0)
        self.mem_copy_util = min(100.0, self.util * 0.45 + random.uniform(0, 2))

    @property
    def fb_used(self) -> int:
        return int(MODEL_WEIGHTS_MIB + max(0.0, self.kv_mib))

    @property
    def fb_free(self) -> int:
        return max(0, MEM_TOTAL_MIB - self.fb_used - 350)


class Simulator:
    def __init__(self) -> None:
        self.gpus = [Gpu(i) for i in range(COUNT)]
        self.lock = threading.Lock()
        self.last_tokens: float | None = None
        self.last_requests: float | None = None
        self.last_t = time.monotonic()
        self.source_ok = False

    def _poll_source(self) -> tuple[float, float] | None:
        try:
            with urllib.request.urlopen(SOURCE_URL, timeout=2) as resp:
                data = json.loads(resp.read().decode())
            prof = data["profiles"][SOURCE_PROFILE]
            return float(prof["tokens_out"]) + 25.0 * float(prof.get("embeddings", 0)), \
                float(prof.get("in_flight", 0))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _synthetic_load(self) -> tuple[float, float]:
        # Office-hours pattern in local time of the container (UTC by default).
        hour = (time.time() / 3600.0) % 24.0
        day = max(0.0, math.sin((hour - 7.0) / 12.0 * math.pi))
        load = 0.08 + 0.55 * day + random.uniform(-0.05, 0.05)
        return max(0.0, min(1.0, load)), 4.0 * load

    def tick(self) -> None:
        now = time.monotonic()
        dt = max(0.5, now - self.last_t)
        self.last_t = now
        sample = self._poll_source()
        if sample is None:
            self.source_ok = False
            load, in_flight = self._synthetic_load()
        else:
            self.source_ok = True
            tokens, in_flight = sample
            if self.last_tokens is None or tokens < self.last_tokens:
                rate = 0.0
            else:
                rate = (tokens - self.last_tokens) / dt
            self.last_tokens = tokens
            load = min(1.0, rate / CAPACITY_TPS + in_flight * 0.06)
        with self.lock:
            for gpu in self.gpus:
                gpu.step(load, in_flight, dt)

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # keep the exporter alive whatever happens
                print(json.dumps({"level": "error", "msg": "tick failed", "error": str(exc)}),
                      flush=True)
            time.sleep(TICK_S)

    def exposition(self) -> str:
        metrics = [
            ("DCGM_FI_DEV_SM_CLOCK", "gauge", "SM clock frequency (in MHz).",
             lambda g: round(210 + (1695 - 210) * min(1.0, g.util / 40.0))),
            ("DCGM_FI_DEV_MEM_CLOCK", "gauge", "Memory clock frequency (in MHz).",
             lambda g: 405 if g.util < 3 else 7600),
            ("DCGM_FI_DEV_MEMORY_TEMP", "gauge", "Memory temperature (in C).",
             lambda g: round(g.mem_temp)),
            ("DCGM_FI_DEV_GPU_TEMP", "gauge", "GPU temperature (in C).",
             lambda g: round(g.temp)),
            ("DCGM_FI_DEV_POWER_USAGE", "gauge", "Power draw (in W).",
             lambda g: round(g.power, 3)),
            ("DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION", "counter",
             "Total energy consumption since boot (in mJ).", lambda g: int(g.energy_mj)),
            ("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", "counter", "Total number of PCIe retries.",
             lambda g: 0),
            ("DCGM_FI_DEV_GPU_UTIL", "gauge", "GPU utilization (in %).",
             lambda g: round(g.util)),
            ("DCGM_FI_DEV_MEM_COPY_UTIL", "gauge", "Memory utilization (in %).",
             lambda g: round(g.mem_copy_util)),
            ("DCGM_FI_DEV_ENC_UTIL", "gauge", "Encoder utilization (in %).", lambda g: 0),
            ("DCGM_FI_DEV_DEC_UTIL", "gauge", "Decoder utilization (in %).", lambda g: 0),
            ("DCGM_FI_DEV_XID_ERRORS", "gauge", "Value of the last XID error encountered.",
             lambda g: 0),
            ("DCGM_FI_DEV_FB_FREE", "gauge", "Frame buffer memory free (in MB).",
             lambda g: g.fb_free),
            ("DCGM_FI_DEV_FB_USED", "gauge", "Frame buffer memory used (in MB).",
             lambda g: g.fb_used),
        ]
        lines: list[str] = []
        with self.lock:
            for name, mtype, help_text, fn in metrics:
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {mtype}")
                for g in self.gpus:
                    labels = (f'gpu="{g.index}",UUID="{g.uuid}",pci_bus_id="{g.pci_bus_id}",'
                              f'device="nvidia{g.index}",modelName="{MODEL_NAME}",'
                              f'Hostname="{HOSTNAME}",DCGM_FI_DRIVER_VERSION="{DRIVER}",'
                              f'simulated="true"')
                    lines.append(f"{name}{{{labels}}} {fn(g)}")
            lines.append("# HELP factory_gpu_sim_source_up 1 when the utilisation follows mock-llm.")
            lines.append("# TYPE factory_gpu_sim_source_up gauge")
            lines.append(f"factory_gpu_sim_source_up {1 if self.source_ok else 0}")
        return "\n".join(lines) + "\n"


SIM = Simulator()


class Handler(BaseHTTPRequestHandler):
    server_version = "gpu-sim/1.0"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path in ("/metrics", "/metrics/"):
            body = SIM.exposition().encode()
            ctype = "text/plain; version=0.0.4; charset=utf-8"
            status = 200
        elif self.path == "/healthz":
            body, ctype, status = b"ok\n", "text/plain", 200
        else:
            body, ctype, status = b"not found\n", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # silence per-request logs
        return


def main() -> None:
    SIM.tick()
    threading.Thread(target=SIM.run, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(json.dumps({"level": "info", "msg": "gpu-sim listening", "port": PORT, "gpus": COUNT,
                      "source": SOURCE_URL}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
