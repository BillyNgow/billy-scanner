from __future__ import annotations
import pathlib, sys
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("AV_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "USE_IBKR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield

@pytest.fixture(autouse=True)
def reset_scanner_counters():
    mod = sys.modules.get("billy_options_scanner")
    if mod is not None:
        if hasattr(mod, "AV_PRE_PROBE_CALLS"): mod.AV_PRE_PROBE_CALLS = 0
        if hasattr(mod, "AV_CALL_COUNT"): mod.AV_CALL_COUNT = 0
    yield
    mod = sys.modules.get("billy_options_scanner")
    if mod is not None:
        if hasattr(mod, "AV_PRE_PROBE_CALLS"): mod.AV_PRE_PROBE_CALLS = 0
        if hasattr(mod, "AV_CALL_COUNT"): mod.AV_CALL_COUNT = 0

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
    def json(self):
        return self._payload

@pytest.fixture
def fake_av_ok(monkeypatch):
    import billy_health
    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(200, {"Global Quote": {"05. price": "123.45"}})
    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get

@pytest.fixture
def fake_av_rate_limited(monkeypatch):
    import billy_health
    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(200, {"Note": "Thank you for using Alpha Vantage..."})
    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get

@pytest.fixture
def fake_av_http_error(monkeypatch):
    import billy_health
    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(503, {})
    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get
