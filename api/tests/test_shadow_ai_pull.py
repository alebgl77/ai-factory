import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "shadow_ai_pull.py"
SPEC = importlib.util.spec_from_file_location("shadow_ai_pull", SCRIPT)
pull = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pull)

SOURCE = "https://shadow.example.test"
FACTORY = "https://factory.example.test"
UPSTREAM = {
    "window_days": 30,
    "window_start": "2026-09-01T16:31:10.123456Z",
    "window_end": "2026-10-01T16:31:10.123456+00:00",
    "total_events": 5600,
    "unique_users": 120,
    "unique_devices": 87,
    "by_category": [{"category": "assistant", "events": 5}],
    "by_source": [{"source_type": "dns", "evidence_type": "observation", "events": 5}],
    "models": [{"provider": "example", "model": "non-exported", "events": 1}],
    "measurement": {"input_tokens": 55},
}
PROJECTION = {
    "window_start": "2026-09-01T16:31:10.123456+00:00",
    "window_end": "2026-10-01T16:31:10.123456+00:00",
    "events": 5600,
    "unique_users": 120,
}
MONTHLY = {
    "period": "2026-09",
    "team_usage": [{"team": "support", "tool": "assistant-x", "kind": "observation", "people": 18, "events": 86}],
    "adoption": [{"team": "support", "tool": "assistant-interne", "active": 12, "eligible": 23}],
}


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class Opener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return next(self.responses)


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("SHADOW_AI_SOURCE_TOKEN", "source.credential")
    monkeypatch.delenv("SHADOW_AI_FACTORY_TOKEN", raising=False)
    monkeypatch.delenv("SHADOW_AI_FACTORY_SESSION", raising=False)

    def configure(*responses):
        opener = Opener(*responses)
        monkeypatch.setattr(pull, "_secure_opener", lambda: opener)
        return opener

    return configure


def test_stdout_projects_only_evidence(transport, capsys):
    opener = transport(Response(json.dumps(UPSTREAM).encode()))
    assert pull.main(["--source", SOURCE]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == PROJECTION
    assert not output.err
    [(request, timeout)] = opener.requests
    assert request.get_method() == "GET"
    assert request.full_url == f"{SOURCE}/api/v1/dashboard/evidence?days=30"
    assert request.get_header("Authorization") == "Bearer source.credential"
    assert timeout == pull.REQUEST_TIMEOUT


@pytest.mark.parametrize(
    ("credential", "header", "value"),
    [
        ("SHADOW_AI_FACTORY_TOKEN", "Authorization", "Bearer factory.credential"),
        ("SHADOW_AI_FACTORY_SESSION", "Cookie", "__Host-factory-sso=opaque==|1790888800|signature="),
    ],
)
def test_delivery_uses_distinct_credentials(transport, capsys, monkeypatch, credential, header, value):
    monkeypatch.setenv(credential, value.removeprefix("Bearer "))
    opener = transport(
        Response(json.dumps(UPSTREAM).encode()),
        Response(b'{"status":"stored"}'),
    )
    assert pull.main(["--source", SOURCE, "--factory", FACTORY]) == 0
    [(source, _), (factory, _)] = opener.requests
    assert source.get_header("Authorization") == "Bearer source.credential"
    assert source.get_header("Cookie") is None
    assert factory.full_url == f"{FACTORY}/api/v1/shadow-ai/evidence"
    assert factory.get_method() == "POST"
    assert factory.get_header(header) == value
    assert factory.get_header("X-factory-user") is None
    assert json.loads(factory.data) == PROJECTION
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "url",
    [
        "http://shadow.example.test",
        "https://user:secret@shadow.example.test",
        "https://shadow.example.test/path",
        "https://shadow.example.test?#",
        "https://shadow.example.test#",
        "https://shadow.example.test:0",
        "https://shadow.example.test:65536",
        "https://shadow.example.test\\@second.example.test",
        "https://shadow.example.test\n",
        "https://shadow..example.test",
    ],
)
def test_non_origin_urls_are_rejected(url):
    with pytest.raises(pull.BridgeError, match="HTTPS origin"):
        pull.https_origin(url)


def test_origin_allows_valid_port_and_ipv6():
    assert pull.https_origin("https://[2001:db8::1]:8443/") == "https://[2001:db8::1]:8443"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target", [SOURCE, FACTORY])
def test_opener_never_redirects(code, target, monkeypatch):
    opener = build_opener(pull.NoRedirect())
    request = Request(SOURCE, headers={"Authorization": "Bearer source.credential"})
    headers = Message()
    headers["Location"] = target
    monkeypatch.setattr(opener, "open", lambda *args, **kwargs: pytest.fail("redirect attempted"))
    with pytest.raises(HTTPError) as error:
        opener.error("https", request, io.BytesIO(), code, "redirect", headers)
    assert error.value.code == code
    error.value.close()


@pytest.mark.parametrize(
    "override",
    [
        {"unique_users": True},
        {"unique_users": -1},
        {"unique_users": 6000},
        {"total_events": "5600"},
        {"window_end": "2026-10-01T16:31:10.123456"},
        {"window_end": "2026-10-02T16:31:10.123456+00:00"},
    ],
)
def test_malformed_evidence_never_leaves_source(override):
    with pytest.raises(pull.BridgeError, match="invalid evidence rollup"):
        pull.project_evidence({**UPSTREAM, **override}, 30)


@pytest.mark.parametrize(
    ("body", "content_type", "reason"),
    [
        (b" " * (pull.MAX_RESPONSE_BYTES + 1), "application/json", "size limit"),
        (b"<html>private response</html>", "text/html", "did not return JSON"),
        (b"invalid private response", "application/json", "invalid JSON"),
    ],
)
def test_response_errors_do_not_echo_body(transport, capsys, body, content_type, reason):
    transport(Response(body, content_type))
    assert pull.main(["--source", SOURCE]) == 1
    out = capsys.readouterr()
    assert not out.out
    assert reason in out.err
    assert "private response" not in out.err


def test_missing_or_conflicting_factory_credentials_do_not_make_requests(transport, capsys, monkeypatch):
    opener = transport()
    assert pull.main(["--source", SOURCE, "--factory", FACTORY]) == 1
    monkeypatch.setenv("SHADOW_AI_FACTORY_TOKEN", "factory.credential")
    monkeypatch.setenv("SHADOW_AI_FACTORY_SESSION", "__Host-factory-sso=managed.session")
    assert pull.main(["--source", SOURCE, "--factory", FACTORY]) == 1
    assert not opener.requests
    assert "credential" in capsys.readouterr().err


def test_empty_explicit_destination_does_not_print_private_projection(transport, capsys):
    opener = transport()
    assert pull.main(["--source", SOURCE, "--factory", ""]) == 1
    assert not opener.requests
    output = capsys.readouterr()
    assert not output.out
    assert "HTTPS origin" in output.err


def test_cli_does_not_repeat_unrecognized_arguments(capsys):
    with pytest.raises(SystemExit) as error:
        pull.main(["--source", SOURCE, "--unknown", "accidental-secret"])
    assert error.value.code == 2
    assert "accidental-secret" not in capsys.readouterr().err


def test_monthly_delivery_requires_only_factory_credentials(transport, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SHADOW_AI_SOURCE_TOKEN")
    monkeypatch.setenv("SHADOW_AI_FACTORY_TOKEN", "factory.credential")
    period = tmp_path / "period.json"
    period.write_text(json.dumps(MONTHLY), encoding="utf-8")
    opener = transport(Response(b'{"status":"stored"}'))
    assert pull.main(["--period-file", str(period), "--factory", FACTORY]) == 0
    [(request, _)] = opener.requests
    assert request.full_url == f"{FACTORY}/api/v1/shadow-ai/periods"
    assert json.loads(request.data) == MONTHLY
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({**MONTHLY, "employees": ["private"]}),
        json.dumps({**MONTHLY, "team_usage": [{**MONTHLY["team_usage"][0], "email": "private@example.test"}]}),
        json.dumps({**MONTHLY, "team_usage": [{**MONTHLY["team_usage"][0], "team": "private@example.test"}]}),
        json.dumps({**MONTHLY, "adoption": [{**MONTHLY["adoption"][0], "active": ["private"]}]}),
        '{"period":"2026-09","team_usage":[{"email":"private"}],"team_usage":[],"adoption":[]}',
        " " * (pull.MAX_PERIOD_BYTES + 1),
    ],
)
def test_monthly_input_refuses_non_aggregate_data(transport, tmp_path, monkeypatch, capsys, content):
    monkeypatch.setenv("SHADOW_AI_FACTORY_TOKEN", "factory.credential")
    opener = transport()
    period = tmp_path / "period.json"
    period.write_text(content, encoding="utf-8")
    assert pull.main(["--period-file", str(period), "--factory", FACTORY]) == 1
    assert not opener.requests
    assert "private" not in capsys.readouterr().err
