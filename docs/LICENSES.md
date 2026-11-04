# Licenses and compatibility

*[Version française](fr/LICENSES.md)*

This repository is published under the **MIT license** ([LICENSE](../LICENSE)). This document
explains why each third-party component is compatible with that choice, what is checked
automatically, and what would change if someone modified an AGPL service. It is not legal
advice.

The reference inventory is `licenses/third_party.yaml` (images, code embedded at build time,
tools, policy); the check is `scripts/check_licenses.py` (run by the license job of CI).

## Principles

1. **All code written for the factory is MIT**: aggregation API, 3D dashboard, simulators,
   LiteLLM callback, scripts, configuration and Grafana JSON dashboards.
2. **Dependencies linked to the code** (Python libraries of `api/` and `sim/`): permissive
   licenses only. Single accepted exception: MPL-2.0 (weak, file-level copyleft) for `certifi`,
   used unmodified.
3. **Third-party services**: each service runs in **its own container**, from **the official,
   unmodified image**, and only communicates with the factory over the network (HTTP, gRPC, SQL
   protocol). No code from these services is copied, linked or recompiled in this repository.
4. **AGPL-3.0 (Grafana OSS, Loki)**: accepted **only** as a separate, unmodified service. The
   browser never talks to Grafana; the API queries it server side, through its HTTP API.
5. **Code embedded at build time**: Three.js (MIT), fetched and verified when the dashboard image
   is built, never committed.
6. **External projects**: git submodules under their own license (MIT or Apache-2.0), never
   copied (see [GITHUB_INTEGRATION.md](GITHUB_INTEGRATION.md)).

## Compatibility matrix

| License | Factory components | How they are used | Compatible with an MIT repository | Obligations for the factory |
|---|---|---|---|---|
| MIT, BSD-2/3-Clause, ISC, PSF-2.0, PostgreSQL | FastAPI, Pydantic, PyYAML, httpx, Uvicorn, Starlette, typing-extensions, LiteLLM (core), Langfuse (core), Ollama, nginx, PostgreSQL, Valkey, Three.js | linked libraries, images, code embedded at build time | yes | keep the copyright and license notices (Three.js: `LICENSE` copied to `/vendor/three` and the `@license` header of the files) |
| Apache-2.0 | prometheus-client, opentelemetry-api, tzdata, Caddy, Prometheus, Alloy, Qdrant, ClickHouse, SeaweedFS, vLLM, dcgm-exporter, submodules open-shadow-ai and openspanguard | linked libraries, images, submodules | yes | keep `LICENSE` and `NOTICE` when redistributing; flag modified files (none here) |
| MPL-2.0 | `certifi` (dependency of httpx) | linked library, **unmodified** | yes | if an MPL file were modified, publish that modified file under MPL-2.0 |
| AGPL-3.0 | Grafana OSS 13.0.2, Loki 3.7.9 | **separate service**, official unmodified image, network calls | yes, with no effect on the repository's license | state the license and the source (done in `licenses/third_party.yaml`); if modified, see below |
| GPL-2.0 / GPL-3.0 / LGPL | packages of the base images (BusyBox, Bash, coreutils, glibc…) | aggregate in the images, no linking with our code | yes (mere aggregation) | if you **publish** built images, provide access to the sources of these packages (Debian / Alpine source archives) |
| MIT license with commercial directories | LiteLLM (`enterprise/`), Langfuse (`ee` directories) | separate service, **without a license key** | yes | do not set `LITELLM_LICENSE` or `LANGFUSE_EE_LICENSE_KEY`: the commercial features stay inactive |
| NVIDIA proprietary licenses (CUDA) | libraries of the `vllm/vllm-openai` and `nvidia/dcgm-exporter` images (`local-gpu` profile) | official images pulled by the user, not redistributed | yes | accept the NVIDIA terms on the GPU machine; do not republish these images |
| SSPL, RSAL, BUSL, Elastic, Commons Clause | none (rejected) | — | not used | Redis ≥ 7.4 replaced by **Valkey** (BSD-3-Clause); MinIO (AGPL) replaced by **SeaweedFS** (Apache-2.0) |

## Why Grafana and Loki (AGPL-3.0) do not "contaminate" the repository

- **What the repository does**: `docker-compose.yml` references the official images
  `grafana/grafana-oss:13.0.2` and `grafana/loki:3.7.9`. It gives them configuration
  (environment variables, provisioning files, JSON dashboards versioned in
  `compose/grafana/dashboards/`). The aggregation API calls the Grafana HTTP API with the token
  of a *Viewer* service account, server side; Alloy pushes logs to Loki over HTTP. Plugin
  installation is disabled (`GF_PLUGINS_PREINSTALL_DISABLED`).
- **What the repository does not do**: it does not copy, modify, recompile or link any Grafana or
  Loki code; it does not publish any derived image.
- **Reasoning**: the AGPL copyleft applies to works based on the program. Separate programs
  communicating over the network, each in its own process and container, form an **aggregate**,
  not a combined work. The JSON dashboards and provisioning files are input data read by
  Grafana: they are our works, under MIT.
- **Remaining obligation**: the AGPL network clause (section 13) only covers **modified**
  versions. With the official unmodified image, the corresponding source code is the one
  published by Grafana Labs (`source` in `licenses/third_party.yaml`).

## What changes if someone modifies Grafana (or Loki)

A modification is: a patch to the source code, an image rebuilt with modified code or UI, a
plugin compiled into the binary, a *fork* of the UI. These are not: configuration through
environment variables, provisioning files, JSON dashboards, an official plugin installed as is
(which keeps its own license, to be checked before adding it).

In case of modification:

1. the modified version must be distributed under **AGPL-3.0**;
2. **section 13**: every user interacting with it over the network must be offered its
   **corresponding source code** (for example a visible link in the UI to the *fork*'s
   repository);
3. the modification must live in **a separate AGPL repository**, never in this MIT repository:
   this repository can keep orchestrating the modified service as a separate container;
4. in `licenses/third_party.yaml`, set the entry to `modified: true` and point `source` to the
   *fork*: `check_licenses.py` fails until these conditions are reviewed (a modified AGPL entry
   is a violation of the default policy);
5. respect the Grafana trademarks (do not present the *fork* as a Grafana Labs product).

Grafana Enterprise is distributed under another (proprietary) license: the factory uses
`grafana/grafana-oss`.

## Three.js (MIT), embedded at build time

`dashboard/Dockerfile` downloads `three@0.186.1` with `npm pack`, checks its SRI integrity (pinned
sha512), copies only the files used and the `LICENSE` file to `/vendor/three`, then rewrites the
*import map* of `index.html` to that local copy. Nothing is committed (`dashboard/vendor/` is
ignored by git). In `file://` mode, the browser loads the same files from the jsDelivr CDN.
`check_licenses.py` checks that the Dockerfile version matches the inventory.

## Python dependencies of the services written for the factory

Snapshot of 2026-11-03 (`python scripts/check_licenses.py` gives the current state):

| Scope | Packages | Licenses |
|---|---|---|
| runtime (`api/`, `sim/`) | fastapi, pydantic, pydantic-core, PyYAML, anyio, h11, annotated-doc, annotated-types, typing-inspection | MIT |
| runtime | uvicorn, httpx, httpcore, starlette, click, idna | BSD-3-Clause |
| runtime | tzdata, opentelemetry-api | Apache-2.0 |
| runtime | prometheus-client | Apache-2.0 AND BSD-2-Clause |
| runtime | typing-extensions | PSF-2.0 |
| runtime | certifi | MPL-2.0 (OK on condition: unmodified) |
| tests (`api/requirements-dev.txt`) | pytest, pluggy, iniconfig | MIT |
| tests | packaging | Apache-2.0 OR BSD-2-Clause |
| tests | Pygments | BSD-2-Clause |

## Visual pack and image tooling

- **Generated images** (`dashboard/assets/`, optional): produced with ChatGPT, by the author
  for this repository (or by you if you regenerate the pack). **By decision of the author, the
  images of the visual pack are published under the repository's MIT license**, like the code. Each image is traced in `dashboard/assets/PROVENANCE.md` with the **SHA-256** hash of
  its source file, the generator and the date. Under OpenAI's terms of use, the user owns the
  generated images and may publish them (finding based on secondary sources, to verify against
  the terms in force). The prompts request no real person, brand or existing work
  ([ASSETS.md](ASSETS.md)). Copyright protection of a fully generated image is not guaranteed in
  every country: this does not hinder redistribution.
- **Pillow** (`scripts/requirements-assets.txt`, MIT-CMU license, permissive): only used by
  `scripts/build_assets.py` when the pack is built; it is not embedded in the service images.

## Submodules (`integrations/`)

| Submodule | License | Pinned commit |
|---|---|---|
| grafana-llmops-forge, dsh-plugin-otel-genai, tierdecay, harnessmeter, promptor | MIT | see `git submodule status` |
| open-shadow-ai, openspanguard | Apache-2.0 | see `git submodule status` |

A submodule is a **reference** (a commit of another repository): its code is not part of this
repository's tree and is fetched from the original repository, under its own license. If you
redistribute an archive that includes the submodules, keep their `LICENSE` and `NOTICE` files.
Forks are never integrated.

## Automated check

`python scripts/check_licenses.py` prints a table and exits **with an error (code 1)** if:

- the repository's license is not MIT;
- a Python dependency of `api/`, `sim/*` or `compose/*` is not permissive (MPL-2.0 tolerated on
  condition) or has an unknown license. Sources, in order: installed metadata (`api/.venv` or the
  current interpreter), PyPI, license declared as a comment in the requirements file; the
  inventory's `python_overrides` for a case checked by hand;
- an image of `docker-compose.yml` or a `FROM` of a Dockerfile of the repository is not
  inventoried in `licenses/third_party.yaml`, or has no valid source URL or usage;
- an AGPL image is not an unmodified `separate-service`; a rejected license appears;
- the Three.js version of the Dockerfile differs from the inventory;
- a submodule has no card, or its card is not `integrated`, or its URL, its license (`LICENSE`
  file read when the submodule is checked out), its secret scan or its copyleft dependencies
  are failing.

Options: `--offline` (no PyPI request), `--json`, `--strict` (warnings count as violations, for
example a submodule not checked out or an image tag that differs from the inventory),
`--self-test` (built-in tests of the policy engine).

**Adding a component**: register the image (SPDX, usage, `modified`, source) in
`licenses/third_party.yaml`, or the dependency in the requirements file with its license as a
comment, then run the script. For an external GitHub repository, use
`scripts/inventory_github.py` (license, dependencies, secrets, card and submodule).
