import importlib.util
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config_loader import ConfigStore, load_config
from app.engines.risk import reevaluation
from app.state import StateError, StateStore, VersionConflict, apply_registry_overrides
from tests.conftest import FIXTURE_DIR, TODAY

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build_snapshot.py"


# ---------------------------------------------------------------- state overlay
def test_state_atomic_writes_and_overlay(tmp_path, cfg):
    store = StateStore(str(tmp_path / "s"))
    uc = next(u for u in cfg["usecases"] if u["id"] == "uc-02")
    store.set_model_version(uc, "2026-11", "cloud/new", TODAY)
    files = sorted(p.name for p in (tmp_path / "s").iterdir())
    assert files == [".state.lock", "registry_overrides.json"]  # no temp file left behind
    merged = apply_registry_overrides(cfg["usecases"], store.registry_overrides())
    m = next(u for u in merged if u["id"] == "uc-02")
    assert m["model"]["version"] == "2026-11" and m["model"]["name"] == "cloud/new"
    assert [h["version"] for h in m["model"]["history"]] == ["2026-07", "2026-11"]
    assert m["assessment"]["decision"] == "pending"
    assert m["assessment"]["previous_decision"] == "approved_with_conditions"
    # the Git baseline is untouched
    assert next(u for u in cfg["usecases"] if u["id"] == "uc-02")["model"]["version"] == "2026-07"
    store.record_assessment(uc, "approved", "p07", TODAY, expected_review_revision=m["review_revision"])
    m2 = next(u for u in apply_registry_overrides(cfg["usecases"], store.registry_overrides()) if u["id"] == "uc-02")
    assert m2["assessment"] == {"reviewed_model_version": "2026-11", "reviewed_revision": m["review_revision"],
                                "last_reviewed": TODAY.isoformat(), "reviewer": "p07", "decision": "approved"}
    assert m2["review_revision"] == m["review_revision"]
    changed = {**uc, "sensitivity": "C4"}
    revised = apply_registry_overrides([changed], store.registry_overrides())[0]
    assert revised["model"] == m2["model"]
    assert reevaluation(revised) == (True, "proposal changed since the last review")


def test_corrupted_state_falls_back_to_baseline(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "registry_overrides.json").write_text("{not json")
    (d / "spaces_state.json").write_text("[]")
    store = StateStore(str(d))
    assert store.registry_overrides() == {}


def test_review_write_refuses_corrupted_registry_instead_of_reusing_baseline_revision(tmp_path, cfg):
    store = StateStore(str(tmp_path))
    uc = cfg["usecases"][0]
    current = apply_registry_overrides([uc], {})[0]
    overlay = tmp_path / "registry_overrides.json"
    overlay.write_bytes(b"{not json")
    with pytest.raises(StateError):
        store.record_assessment(uc, "approved", "p07", TODAY,
                                expected_review_revision=current["review_revision"])
    assert overlay.read_bytes() == b"{not json"


def test_concurrent_model_changes_extend_the_same_history(tmp_path, cfg):
    store = StateStore(str(tmp_path))
    uc = next(u for u in cfg["usecases"] if u["id"] == "uc-02")
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(store.set_model_version, uc, version, None, TODAY)
                   for version in ("2026-11-a", "2026-11-b")]
    proposals = [change.result() for change in changes]
    current = apply_registry_overrides([uc], store.registry_overrides())[0]
    assert {event["version"] for event in current["model"]["history"][-2:]} == {"2026-11-a", "2026-11-b"}
    assert len({proposal["review_revision"] for proposal in proposals}) == 2
    assert current["review_revision"] in {proposal["review_revision"] for proposal in proposals}
    assert store.spaces_state()["version"] == 0


def test_spaces_conflict(tmp_path):
    store = StateStore(str(tmp_path / "s"))
    assert store.put_spaces_state(0, {"a": 1})["version"] == 1
    with pytest.raises(VersionConflict):
        store.put_spaces_state(0, {"a": 2})


# ---------------------------------------------------------------- configuration loader
def test_missing_config_dir_uses_contract_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing")
    assert cfg["usecases"] == [] and cfg["warnings"]
    assert cfg["factory"]["factory"]["fine_tuning"] is False
    assert cfg["factory"]["placement"]["sensitive_classes"] == ["C3", "C4"]
    assert len(cfg["factory"]["waste_rules"]) == 8


def test_malformed_and_duplicate_files(tmp_path):
    shutil.copytree(FIXTURE_DIR, tmp_path / "c")
    (tmp_path / "c" / "usecases" / "zz-broken.yaml").write_text("id: [unclosed")
    (tmp_path / "c" / "usecases" / "zz-dup.yaml").write_text("id: uc-01\nname: dup\n")
    (tmp_path / "c" / "skills.yaml").unlink()
    cfg = load_config(tmp_path / "c")
    assert [u["id"] for u in cfg["usecases"]].count("uc-01") == 1
    # configuration warnings are {en, fr} mappings, resolved per language by localize_tree
    assert all(set(w) == {"en", "fr"} for w in cfg["warnings"])
    assert any("zz-broken" in w["en"] and "zz-broken" in w["fr"] for w in cfg["warnings"])
    assert any("duplicate id uc-01" in w["en"] for w in cfg["warnings"])
    assert any("dupliqué" in w["fr"] for w in cfg["warnings"])
    assert cfg["skills"]["modules"] == []


def test_yaml_dates_are_normalised(cfg):
    uc = next(u for u in cfg["usecases"] if u["id"] == "uc-01")
    assert uc["assessment"]["last_reviewed"] == "2026-06-12"
    json.dumps(cfg)  # fully serialisable


def test_config_store_reloads_on_change(tmp_path):
    shutil.copytree(FIXTURE_DIR, tmp_path / "c")
    store = ConfigStore(str(tmp_path / "c"))
    gen = store.generation
    assert store.get()["factory"]["factory"]["name"].endswith("(fixture)")
    p = tmp_path / "c" / "factory.yaml"
    p.write_text(p.read_text().replace("(fixture)", "(modified)"))
    assert store.get()["factory"]["factory"]["name"].endswith("(modified)")
    assert store.generation > gen + 0


# ---------------------------------------------------------------- scripts/build_snapshot.py
@pytest.fixture(scope="module")
def build_mod():
    spec = importlib.util.spec_from_file_location("build_snapshot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HTML = "<!doctype html><html><head><title>t</title></head><body><p>x</p>\n</body></html>\n"


def test_build_snapshot_inserts_before_body_and_is_idempotent(tmp_path, build_mod):
    html = tmp_path / "index.html"
    html.write_text(HTML)
    args = ["--config", str(FIXTURE_DIR), "--html", str(html), "--date", "2026-11-03", "--seed", "42"]
    assert build_mod.main(args) == 0
    text = html.read_text()
    assert text.count(build_mod.START) == 1 and text.count(build_mod.END) == 1
    assert text.index(build_mod.END) < text.index("</body>")
    block = text[text.index(build_mod.START):text.index(build_mod.END)]
    en_tag = '<script type="application/json" id="demo-snapshot-en">'
    fr_tag = '<script type="application/json" id="demo-snapshot-fr">'
    assert block.count(en_tag) == 1 and block.count(fr_tag) == 1
    assert block.index(en_tag) < block.index(fr_tag)  # English first
    assert 'id="demo-snapshot">' not in text
    first = text
    assert build_mod.main(args) == 0
    assert html.read_text() == first
    snaps = build_mod.embedded_snapshots(first)
    for lang in ("en", "fr"):
        snap = snaps[lang]
        assert snap["meta"]["lang"] == lang and snap["meta"]["languages"] == ["en", "fr"]
        assert snap["meta"]["mode"] == "demo" and snap["meta"]["anchor_date"] == "2026-11-03"
        assert snap["meta"]["generated_at"] == "2026-11-03T10:00:00+01:00"
    assert snaps["en"]["registry"][0]["name"] == "Document RAG assistant (fictional)"
    assert snaps["fr"]["registry"][0]["name"] == "Assistant RAG documentaire (fictif)"
    assert build_mod.main(["--config", str(FIXTURE_DIR), "--html", str(html), "--check"]) == 0


def test_build_snapshot_replaces_legacy_single_block(tmp_path, build_mod):
    html = tmp_path / "index.html"
    legacy = (f"{build_mod.START}\n<script type=\"application/json\" id=\"demo-snapshot\">{{}}</script>\n"
              f"{build_mod.END}")
    html.write_text(HTML.replace("</body>", legacy + "\n</body>"))
    # the legacy block is not accepted by --check
    assert build_mod.main(["--config", str(FIXTURE_DIR), "--html", str(html), "--check"]) == 1
    assert build_mod.main(["--config", str(FIXTURE_DIR), "--html", str(html), "--date", "2026-11-03"]) == 0
    text = html.read_text()
    assert 'id="demo-snapshot">' not in text and text.count(build_mod.START) == 1
    assert all(build_mod.embedded_snapshots(text).values())
    assert build_mod.main(["--config", str(FIXTURE_DIR), "--html", str(html), "--check"]) == 0


def test_build_snapshot_check_detects_drift(tmp_path, build_mod):
    conf = tmp_path / "c"
    shutil.copytree(FIXTURE_DIR, conf)
    html = tmp_path / "index.html"
    html.write_text(HTML)
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--date", "2026-11-03"]) == 0
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--check"]) == 0
    # a change of the French text only must be detected (both blocks are verified)
    p = conf / "usecases" / "uc-03.yaml"
    p.write_text(p.read_text().replace("Classification de tickets", "Classification de courriels"))
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--check"]) == 1
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--date", "2026-11-03"]) == 0
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--check"]) == 0
    # a block removed by hand is detected too
    text = html.read_text()
    a = text.index('<script type="application/json" id="demo-snapshot-fr">')
    b = text.index("</script>", a) + len("</script>")
    html.write_text(text[:a] + text[b:])
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--check"]) == 1
    empty = tmp_path / "empty.html"
    empty.write_text(HTML)
    assert build_mod.main(["--config", str(conf), "--html", str(empty), "--check"]) == 1


def test_build_snapshot_escapes_closing_tags(tmp_path, build_mod):
    conf = tmp_path / "c"
    shutil.copytree(FIXTURE_DIR, conf)
    p = conf / "usecases" / "uc-03.yaml"
    evil = "Tickets </script><script>alert(1)</script> <!-- x"
    p.write_text(p.read_text().replace('"Classification de tickets (fictif)"', f'"{evil}"')
                 .replace('"Ticket classification (fictional)"', f'"{evil}"'))
    html = tmp_path / "index.html"
    html.write_text(HTML)
    assert build_mod.main(["--config", str(conf), "--html", str(html), "--date", "2026-11-03"]) == 0
    text = html.read_text()
    block = text[text.index(build_mod.START):text.index(build_mod.END)]
    assert block.count("</script>") == 2  # only the real closing tags (one per language)
    assert "<!-- x" not in block
    for lang in ("en", "fr"):
        snap = build_mod.embedded_snapshot(text, lang)
        assert any("</script>" in (u["name"] or "") for u in snap["registry"])


def test_build_snapshot_out_matches_api_demo(tmp_path, build_mod, make_client):
    html = tmp_path / "index.html"
    html.write_text(HTML)
    client = make_client()
    for lang in (None, "en", "fr"):
        out = tmp_path / f"snap-{lang}.json"
        args = ["--config", str(FIXTURE_DIR), "--out", str(out), "--date", "2026-11-03"]
        assert build_mod.main(args + (["--lang", lang] if lang else [])) == 0
        assert html.read_text() == HTML  # --out alone leaves the HTML untouched
        data = json.loads(out.read_text())
        assert data["meta"]["lang"] == (lang or "en")
        api = client.get("/api/v1/snapshot", params={"lang": lang} if lang else None).json()
        for key in ("registry", "costs", "pipeline", "dataloop", "placement", "agentops", "catalog", "skills",
                    "gpu", "factory", "profiles", "frameworks"):
            assert data[key] == api[key], (lang, key)


def test_build_snapshot_lang_requires_out(tmp_path, build_mod):
    html = tmp_path / "index.html"
    html.write_text(HTML)
    assert build_mod.main(["--config", str(FIXTURE_DIR), "--html", str(html), "--lang", "fr"]) == 2
    assert html.read_text() == HTML


def test_build_snapshot_missing_config(tmp_path, build_mod):
    assert build_mod.main(["--config", str(tmp_path), "--out", str(tmp_path / "x.json")]) == 2


# ---------------------------------------------------------------- scripts/inventory_github.py
@pytest.fixture
def inventory_mod(monkeypatch):
    path = REPO / "scripts" / "inventory_github.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("inventory_github", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(("triaged", "scan_status", "decision"), [
    (False, "findings", "excluded"),
    (True, "clean", "integrated"),
])
def test_inventory_reports_only_scan_summary(tmp_path, capsys, monkeypatch, inventory_mod,
                                             triaged, scan_status, decision):
    sentinel = "synthetic-sensitive-scanner-text"
    repo_file = tmp_path / "inventory.yaml"
    repo_file.write_text("owner: owner\nrepos:\n  - name: fixture\n    pillar: rag\n", encoding="utf-8")
    triage = tmp_path / "triage.yaml"
    if triaged:
        triage.write_text(
            inventory_mod.yaml.safe_dump({"triage": [{
                "repo": "fixture", "commit": "abc1234", "file": f"{sentinel}.txt",
                "line": 1, "rule": sentinel, "reason": sentinel, "verdict": "false_positive",
            }]}),
            encoding="utf-8",
        )
    cards = tmp_path / "cards"
    monkeypatch.setattr(inventory_mod, "ROOT", tmp_path)
    monkeypatch.setattr(inventory_mod, "CARDS_DIR", cards)
    monkeypatch.setattr(inventory_mod, "TRIAGE", triage)
    monkeypatch.setattr(inventory_mod, "clone_full", lambda *_: None)
    monkeypatch.setattr(inventory_mod, "git_info", lambda _: {})
    monkeypatch.setattr(inventory_mod, "repo_license", lambda _: {"spdx": "MIT"})
    monkeypatch.setattr(inventory_mod.shutil, "which", lambda name: name if name == "gitleaks" else None)

    def run_scanner(cmd, **kwargs):
        report = Path(cmd[cmd.index("--report-path") + 1])
        report.write_text(json.dumps([{
            "RuleID": sentinel, "File": f"{sentinel}.txt", "Commit": "abc1234", "StartLine": 1,
            "Secret": sentinel, "Match": sentinel,
        }]), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout=sentinel, stderr="12 commits scanned")

    monkeypatch.setattr(inventory_mod, "run", run_scanner)
    report, markdown, work = (tmp_path / name for name in ("report.json", "report.md", "work"))
    assert inventory_mod.main(["--repos-file", str(repo_file), "--workdir", str(work), "--offline",
                               "--no-deps", "--write-cards", "--report", str(report),
                               "--markdown", str(markdown)]) == 0
    report_text = report.read_text(encoding="utf-8")
    markdown_text = markdown.read_text(encoding="utf-8")
    assert "12 commits, 1 brut(s)" in markdown_text
    item, = json.loads(report_text)["repos"]
    assert item["status"] == decision
    assert item["secrets"] == {
        "status": scan_status, "tool": inventory_mod.GITLEAKS_IMAGE, "commits_scanned": 12,
        "raw_findings": 1, "false_positive_count": int(triaged), "remaining_count": int(not triaged),
    }
    output = capsys.readouterr()
    assert scan_status in output.out
    assert sentinel not in report_text + markdown_text + output.out + output.err
    assert not list(work.glob("gitleaks-out-*"))
    if triaged:
        card = (cards / "fixture.yaml").read_text(encoding="utf-8")
        assert sentinel not in card and inventory_mod.yaml.safe_load(card)["secrets_scan"] == "clean"
    else:
        assert not cards.exists()


@pytest.mark.parametrize("scanner_fails", [False, True])
def test_inventory_discards_failed_scanner_report(tmp_path, monkeypatch, inventory_mod, scanner_fails):
    sentinel = "synthetic-sensitive-scanner-text"
    monkeypatch.setattr(inventory_mod.shutil, "which", lambda name: name if name == "gitleaks" else None)

    def run_scanner(cmd, **kwargs):
        Path(cmd[cmd.index("--report-path") + 1]).write_text(sentinel, encoding="utf-8")
        if scanner_fails:
            raise subprocess.TimeoutExpired(cmd, 3600, output=sentinel, stderr=sentinel)
        return subprocess.CompletedProcess(cmd, 0, stdout=sentinel, stderr=sentinel)

    monkeypatch.setattr(inventory_mod, "run", run_scanner)
    assert inventory_mod.gitleaks_summary(tmp_path, tmp_path, [], "fixture") == {
        "status": "not_scanned", "reason": "rapport gitleaks indisponible ou invalide",
    }
    assert not list(tmp_path.glob("gitleaks-out-*"))


@pytest.mark.parametrize("format_args", [[], ["--json"]])
def test_validate_config_reports_invalid_scan_status_without_input(tmp_path, format_args):
    config = tmp_path / "config"
    shutil.copytree(FIXTURE_DIR, config)
    card = config / "catalog" / "github" / "demo-repo.yaml"
    original = card.read_text(encoding="utf-8")
    sentinel = "synthetic-sensitive-scanner-text"
    card.write_text(original.replace("secrets_scan: clean", f"secrets_scan: {sentinel}"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "validate_config.py"), "--config", str(config), *format_args],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 1
    assert "secrets_scan must be clean, findings or not_scanned" in result.stdout + result.stderr
    assert sentinel not in result.stdout + result.stderr
