"""Shared pytest fixtures for Billy scanner tests.

Goals:
- Make the repo root importable so tests can import local modules.
- Run each test in an isolated temporary working directory.
- Unset real secrets by default.
- Stub Alpha Vantage HTTP responses with fake responses.
- Reset scanner AV counters between tests.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# Make repository root importable for tests
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test from a temporary directory.

    This ensures output/health_report_*.json test writes never touch
    the real repository workspace.
    """
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove real secrets from the test environment by default."""
    for var in ("AV_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture(autouse=True)
def reset_scanner_counters():
    """Reset scanner AV counters before and after each test."""
    mod = sys.modules.get("billy_options_scanner")
    if mod is not None:
        if hasattr(mod, "AV_PRE_PROBE_CALLS"):
            mod.AV_PRE_PROBE_CALLS = 0
        if hasattr(mod, "AV_CALL_COUNT"):
            mod.AV_CALL_COUNT = 0

    yield

    mod = sys.modules.get("billy_options_scanner")
    if mod is not None:
        if hasattr(mod, "AV_PRE_PROBE_CALLS"):
            mod.AV_PRE_PROBE_CALLS = 0
        if hasattr(mod, "AV_CALL_COUNT"):
            mod.AV_CALL_COUNT = 0


class _FakeResponse:
    """Minimal stand-in for requests.Response used by AV probe tests."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture
def fake_av_ok(monkeypatch):
    """Make requests.get return a healthy AV GLOBAL_QUOTE response."""
    import billy_health

    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(
            200,
            {"Global Quote": {"05. price": "123.45"}},
        )

    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get


@pytest.fixture
def fake_av_rate_limited(monkeypatch):
    """Make AV respond with a rate-limit / informational payload."""
    import billy_health

    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(
            200,
            {"Note": "Thank you for using Alpha Vantage..."},
        )

    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get


@pytest.fixture
def fake_av_http_error(monkeypatch):
    """Make AV respond with a non-200 HTTP status."""
    import billy_health

    def _get(url, params=None, timeout=None, headers=None):
        return _FakeResponse(503, {})

    monkeypatch.setattr(billy_health.requests, "get", _get)
    return _get
