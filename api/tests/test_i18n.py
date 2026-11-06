"""Internationalisation (CONTRACT section 9): catalogue, localize_tree, ?lang= on every read endpoint."""

from __future__ import annotations

import json
import re
import string
from typing import Any, Iterator

import pytest

from app import i18n
from app.i18n import MESSAGES, is_lang_mapping, localize_tree, msg, normalize_lang
from app.settings import Settings
from tests.conftest import review_body

# Every read endpoint of CONTRACT section 3 (GET), with the ones that carry generated or config text.
READ_ENDPOINTS = ["/api/v1/health", "/api/v1/snapshot", "/api/v1/registry", "/api/v1/registry/uc-01",
                  "/api/v1/costs", "/api/v1/pipeline", "/api/v1/dataloop", "/api/v1/placement",
                  "/api/v1/agentops", "/api/v1/catalog", "/api/v1/skills", "/api/v1/gpu",
                  "/api/v1/grafana/dashboards", "/api/v1/spaces/state"]
TEXT_ENDPOINTS = ["/api/v1/snapshot", "/api/v1/registry", "/api/v1/registry/uc-01", "/api/v1/costs",
                  "/api/v1/pipeline", "/api/v1/dataloop", "/api/v1/placement", "/api/v1/agentops",
                  "/api/v1/catalog", "/api/v1/skills", "/api/v1/gpu", "/api/v1/grafana/dashboards"]

FRENCH_ACCENTS = "àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ"
FRENCH_WORDS = ("le", "la", "les", "des", "du", "une", "et", "pour", "avec", "sans", "sur", "dans", "aucun",
                "aucune", "fictif", "fictive", "indisponible", "uniquement", "introuvable", "modèle", "données",
                "revue", "cas", "requise", "dernière", "réévaluation", "maîtrise", "risque")
FRENCH_RE = re.compile(rf"[{FRENCH_ACCENTS}]|\b(?:{'|'.join(FRENCH_WORDS)})\b|\b[dl]'\w", re.IGNORECASE)

# Last keys whose string value is human-readable text (may differ between languages). Every other
# string (ids, enums, dates, versions, aliases, colours, URLs...) must be identical in en and fr.
TEXT_KEYS = {"name", "label", "title", "tagline", "description", "message", "action", "detail", "reason",
             "reasons", "note", "warnings", "rationale", "evidence_hint", "hypothesis", "transfer_criteria",
             "value_metric", "skills", "provider", "trigger_label", "platform", "showback", "chargeback",
             "usecase_name", "module_title", "annex_iii_use", "evidence", "lang", "owner_name", "unit"}


def strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def mappings(obj: Any, path: str = "") -> Iterator[str]:
    """Paths of every {en, fr} mapping left in a payload (must be none)."""
    if isinstance(obj, dict):
        if is_lang_mapping(obj):
            yield path
        for k, v in obj.items():
            yield from mappings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from mappings(v, f"{path}[{i}]")


def french_leaks(snap: dict[str, Any]) -> list[tuple[str, str]]:
    people = sorted((str(p.get("name")) for p in snap["profiles"]["people"]), key=len, reverse=True)
    leaks = []
    for path, text in strings(snap):
        cleaned = text
        for name in people:  # person names are proper nouns, identical in every language
            cleaned = cleaned.replace(name, "")
        if FRENCH_RE.search(cleaned):
            leaks.append((path, text))
    return leaks


# ------------------------------------------------------------------------------- normalize / msg
@pytest.mark.parametrize("value,expected", [("en", "en"), ("fr", "fr"), ("FR", "fr"), ("fr-FR", "fr"),
                                            ("en_US", "en"), (" fr ", "fr"), ("de", "en"), ("", "en"),
                                            (None, "en"), (42, "en"), ("français", "en")])
def test_normalize_lang(value, expected):
    assert normalize_lang(value) == expected


def test_normalize_lang_custom_default():
    assert normalize_lang("de", "fr") == "fr"
    assert normalize_lang(None, "fr") == "fr"
    assert normalize_lang("xx", "xx") == "en"  # an invalid default falls back to en


def test_catalogue_is_complete_and_consistent():
    fmt = string.Formatter()
    for key, entry in MESSAGES.items():
        assert set(entry) == {"en", "fr"}, key
        assert entry["en"].strip() and entry["fr"].strip(), key
        fields = {lang: {f for _, f, _, _ in fmt.parse(entry[lang]) if f} for lang in ("en", "fr")}
        assert fields["en"] == fields["fr"], key
        assert not re.search(f"[{FRENCH_ACCENTS}]", entry["en"]), key


def test_msg_formats_params_and_numbers_per_language():
    assert msg("en", "risk.reason.model_not_reviewed", version="2026-09", last="2026-06") == \
        "model 2026-09 not reviewed (last 2026-06)"
    assert msg("fr", "risk.reason.model_not_reviewed", version="2026-09", last="2026-06") == \
        "modèle 2026-09 non revu (dernière revue : 2026-06)"
    assert msg("en", "pipeline.gate.below", metric="faithfulness", value=0.86, threshold=0.9).count("0.86") == 1
    assert msg("fr", "pipeline.gate.below", metric="faithfulness", value=0.86, threshold=0.9).count("0,86") == 1
    assert msg("de", "error.internal") == "internal error"  # unknown language -> en
    assert msg("en", "no.such.key") == "no.such.key"  # never raises
    assert msg("en", "risk.reason.model_not_reviewed") == MESSAGES["risk.reason.model_not_reviewed"]["en"]
    assert i18n.both("error.internal") == {"en": "internal error", "fr": "erreur interne"}


# ------------------------------------------------------------------------------- localize_tree
def test_localize_tree_resolves_nested_mappings():
    tree = {"name": {"en": "Operations", "fr": "Opérations"}, "id": "cc-ops", "budget": 1800,
            "tags": [{"en": "Scoping", "fr": "Cadrage"}, "RAG", 3, None, True],
            "nested": {"deep": [{"label": {"fr": "seulement fr"}}]}, "t": ({"en": "a", "fr": "b"},)}
    en = localize_tree(tree, "en")
    fr = localize_tree(tree, "fr")
    assert en == {"name": "Operations", "id": "cc-ops", "budget": 1800, "tags": ["Scoping", "RAG", 3, None, True],
                  "nested": {"deep": [{"label": "seulement fr"}]}, "t": ["a"]}
    assert fr["name"] == "Opérations" and fr["tags"][0] == "Cadrage" and fr["t"] == ["b"]
    # the input is never mutated
    assert tree["name"] == {"en": "Operations", "fr": "Opérations"}


def test_localize_tree_fallbacks():
    assert localize_tree({"en": "only en"}, "fr") == "only en"
    assert localize_tree({"fr": "seulement fr"}, "en") == "seulement fr"
    assert localize_tree({"en": "", "fr": "texte"}, "en") == "texte"  # empty -> other language
    assert localize_tree({"en": None, "fr": "texte"}, "en") == "texte"
    assert localize_tree({"en": "", "fr": ""}, "fr") == ""  # nothing better: first value
    assert localize_tree({"en": {"fr": "x", "en": "y"}}, "fr") == "x"  # mapping inside a mapping
    assert localize_tree("plain", "fr") == "plain"
    assert localize_tree({"en": "a", "fr": "b"}, "de") == "a"  # unknown language -> en


def test_localize_tree_leaves_other_dicts_alone():
    # not a language mapping: other keys present, or empty dict
    assert localize_tree({"en": "a", "id": 1}, "fr") == {"en": "a", "id": 1}
    assert localize_tree({}, "fr") == {}
    assert localize_tree({"EN": "a"}, "fr") == {"EN": "a"}
    assert not is_lang_mapping({}) and is_lang_mapping({"fr": "x"}) and not is_lang_mapping({"en": 1, "de": 2})


def test_fixture_config_holds_mappings_and_plain_strings(cfg):
    assert list(mappings(cfg)), "the fixture must exercise {en, fr} mappings"
    assert isinstance(cfg["factory"]["factory"]["name"], str)  # plain string kept as written
    for lang in ("en", "fr"):
        assert not list(mappings(localize_tree(cfg, lang)))


# ------------------------------------------------------------------------------- API: every read endpoint
@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_every_read_endpoint_accepts_lang_and_is_fully_resolved(client, path):
    bodies = {}
    for lang in ("en", "fr", "de", None):
        r = client.get(path, params={"lang": lang} if lang else None)
        assert r.status_code == 200, (path, lang, r.text)
        body = r.json()
        assert not list(mappings(body)), (path, lang)
        bodies[lang] = body
    assert bodies["de"] == bodies["en"] == bodies[None]  # unknown and absent -> default (en)
    if path in TEXT_ENDPOINTS:
        assert bodies["fr"] != bodies["en"], path


def test_health_reports_languages(client):
    body = client.get("/api/v1/health", params={"lang": "fr"}).json()
    assert body["lang"] == "fr" and body["languages"] == ["en", "fr"] and body["default_lang"] == "en"


def test_snapshot_meta_lang(client):
    for lang in ("en", "fr"):
        meta = client.get("/api/v1/snapshot", params={"lang": lang}).json()["meta"]
        assert meta["lang"] == lang and meta["languages"] == ["en", "fr"]
    assert client.get("/api/v1/snapshot", params={"lang": "es"}).json()["meta"]["lang"] == "en"


def test_default_language_setting(make_client, monkeypatch):
    c = make_client(default_lang="fr")
    assert c.get("/api/v1/snapshot").json()["meta"]["lang"] == "fr"
    assert c.get("/api/v1/snapshot", params={"lang": "xx"}).json()["meta"]["lang"] == "fr"
    assert c.get("/api/v1/snapshot", params={"lang": "en"}).json()["meta"]["lang"] == "en"
    assert c.get("/api/v1/health").json()["default_lang"] == "fr"
    monkeypatch.setenv("FACTORY_DEFAULT_LANG", "fr")
    assert Settings.from_env().default_lang == "fr"
    monkeypatch.setenv("FACTORY_DEFAULT_LANG", "klingon")
    assert Settings.from_env().default_lang == "en"
    monkeypatch.delenv("FACTORY_DEFAULT_LANG")
    assert Settings.from_env().default_lang == "en"
    assert Settings(default_lang="FR").default_lang == "fr"


def test_cache_is_per_language(make_client):
    c = make_client(cache_ttl_s=60)
    en = c.get("/api/v1/snapshot", params={"lang": "en"}).json()
    fr = c.get("/api/v1/snapshot", params={"lang": "fr"}).json()
    assert en["meta"]["lang"] == "en" and fr["meta"]["lang"] == "fr"
    assert c.get("/api/v1/snapshot", params={"lang": "en"}).json() == en


# ------------------------------------------------------------------------------- no leak, same data
def test_no_french_text_in_english_snapshot(client):
    snap = client.get("/api/v1/snapshot", params={"lang": "en"}).json()
    leaks = french_leaks(snap)
    assert not leaks, leaks[:10]


def test_french_snapshot_is_french(client):
    snap = client.get("/api/v1/snapshot", params={"lang": "fr"}).json()
    texts = [t for _, t in strings(snap)]
    assert len(french_leaks(snap)) > 50  # plenty of French text
    reg = {u["id"]: u for u in snap["registry"]}
    assert reg["uc-01"]["name"] == "Assistant RAG documentaire (fictif)"
    assert reg["uc-01"]["risk"]["reason"] == "modèle 2026-09 non revu (dernière revue : 2026-06)"
    assert reg["uc-01"]["model"]["name"] == "cloud/large (fictif)"
    assert any("uniquement" in r for w in snap["placement"]["workloads"] for r in w["reasons"])
    assert {t["id"]: t["label"] for t in snap["placement"]["targets"]}["local"] == "GPU local (vLLM)"
    assert "maîtrise de l'IA" in snap["skills"]["art4"]["note"]
    assert all(re.search(f"[{FRENCH_ACCENTS}]", a["message"] + a["action"]) for a in snap["costs"]["alerts"])
    assert snap["gpu"]["devices"][0]["name"] == "GPU local 24 Go (simulé)"
    assert "Opérations (fictif)" in texts
    en = client.get("/api/v1/snapshot", params={"lang": "en"}).json()
    assert {u["id"]: u for u in en["registry"]}["uc-01"]["risk"]["reason"] == "model 2026-09 not reviewed (last 2026-06)"
    assert en["gpu"]["devices"][0]["name"] == "Local GPU 24 GB (simulated)"


def test_languages_share_ids_numbers_and_dates(client):
    en = client.get("/api/v1/snapshot", params={"lang": "en"}).json()
    fr = client.get("/api/v1/snapshot", params={"lang": "fr"}).json()
    differing: set[str] = set()

    def walk(a: Any, b: Any, path: str, last: str) -> None:
        assert type(a) is type(b), path
        if isinstance(a, dict):
            assert list(a) == list(b), path  # same keys, same order
            for k in a:
                walk(a[k], b[k], f"{path}.{k}", k)
        elif isinstance(a, list):
            assert len(a) == len(b), path
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]", last)
        elif a != b:
            assert isinstance(a, str) and last in TEXT_KEYS, (path, a, b)
            differing.add(last)

    walk(en, fr, "", "")
    assert {"name", "message", "reasons", "detail", "note"} <= differing
    # enums that share a key name with text fields stay identical
    assert [e["action"] for e in en["dataloop"]["events"]] == [e["action"] for e in fr["dataloop"]["events"]]
    assert [a["rule"] for a in en["costs"]["alerts"]] == [a["rule"] for a in fr["costs"]["alerts"]]


# ------------------------------------------------------------------------------- writes and errors
def test_writes_are_language_independent(make_client):
    c = make_client()
    r = c.post("/api/v1/registry/uc-01/model-version", params={"lang": "fr"}, json={"version": "2026-11"})
    assert r.status_code == 200
    body = r.json()
    assert body["message"].startswith("Nouvelle version de modèle enregistrée")
    assert body["reason"] == "modèle 2026-11 non revu (dernière revue : 2026-06) ; évaluation en attente"
    # the {en, fr} model name of the Git baseline survives the state overlay, resolved per language
    assert body["model"]["name"] == "cloud/large (fictif)"
    en = c.get("/api/v1/registry/uc-01").json()
    assert en["model"]["name"] == "cloud/large (fictional)" and en["model"]["version"] == "2026-11"
    assert en["risk"]["reason"] == "model 2026-11 not reviewed (last 2026-06); assessment pending"
    assert en["review_revision"] == body["review_revision"]
    r = c.post("/api/v1/registry/uc-01/assessment", json=review_body(c, "uc-01"))
    assert r.status_code == 200 and r.json()["reason"] is None
    en = c.get("/api/v1/registry/uc-01").json()
    fr = c.get("/api/v1/registry/uc-01", params={"lang": "fr"}).json()
    assert fr["review_revision"] == en["review_revision"] == body["review_revision"]
    assert fr["assessment"] == en["assessment"]
    assert en["assessment"]["decision"] == "approved" and en["assessment"]["reviewed_model_version"] == "2026-11"


def test_placement_decide_reasons_follow_lang(client):
    payload = {"sensitivity": "C3", "params_b": 14}
    en = client.post("/api/v1/placement/decide", json=payload).json()["decision"]
    fr = client.post("/api/v1/placement/decide", params={"lang": "fr"}, json=payload).json()["decision"]
    assert en["target"] == fr["target"] == "local"
    assert en["reasons"][0] == "C3 => local only" and fr["reasons"][0] == "C3 => local uniquement"


@pytest.mark.parametrize("lang,expected", [(None, "use case not found"), ("en", "use case not found"),
                                           ("fr", "cas d'usage introuvable"), ("zz", "use case not found")])
def test_errors_follow_lang(client, lang, expected):
    params = {"lang": lang} if lang else None
    r = client.get("/api/v1/registry/uc-99", params=params)
    assert r.status_code == 404 and r.json()["detail"] == expected


def test_other_errors_are_localised(client):
    assert client.get("/api/v1/grafana/metric/gpu_util", params={"lang": "fr"}).json()["detail"] == \
        "Grafana indisponible (mode démo ou non configuré)"
    assert client.get("/api/v1/grafana/metric/nope").json()["detail"].startswith("unknown query")
    r = client.post("/api/v1/placement/decide", params={"lang": "fr"}, json={})
    assert r.status_code == 422 and r.json()["detail"] == "sensibilité (C1..C4) ou cas d'usage requis"
    r = client.post("/api/v1/registry/uc-01/assessment", json=review_body(client, "uc-01", reviewer="p99"))
    assert r.json()["detail"] == "reviewer not found in profiles.yaml"
    big = json.dumps({"version": 0, "state": {"x": "y" * 70000}})
    r = client.put("/api/v1/spaces/state", params={"lang": "fr"}, content=big,
                   headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "corps de requête trop volumineux"
    r = client.put("/api/v1/spaces/state", content=big, headers={"content-type": "application/json"})
    assert r.json()["detail"] == "request body too large"


def test_spaces_conflict_message_follows_lang(client):
    assert client.put("/api/v1/spaces/state", json={"version": 0, "state": {"a": 1}}).status_code == 200
    r = client.put("/api/v1/spaces/state", params={"lang": "fr"}, json={"version": 0, "state": {"a": 2}})
    assert r.status_code == 409 and r.json()["detail"] == "conflit de version" and r.json()["current"]["version"] == 1
    r = client.put("/api/v1/spaces/state", json={"version": 0, "state": {"a": 2}})
    assert r.json()["detail"] == "version conflict"


def test_config_warnings_are_localised(tmp_path, make_client):
    import shutil

    from tests.conftest import FIXTURE_DIR

    conf = tmp_path / "c"
    shutil.copytree(FIXTURE_DIR, conf)
    (conf / "usecases" / "zz-dup.yaml").write_text("id: uc-01\nname: dup\n")
    c = make_client(config_dir=str(conf))
    en = c.get("/api/v1/snapshot").json()["meta"]["warnings"]
    fr = c.get("/api/v1/snapshot", params={"lang": "fr"}).json()["meta"]["warnings"]
    assert en == ["usecases/zz-dup.yaml: duplicate id uc-01, file ignored"]
    assert fr == ["usecases/zz-dup.yaml : identifiant uc-01 dupliqué, fichier ignoré"]
