"""Shared pytest fixtures for Milestone 1 tests.

Goals:

- Make the repo root importable so import billy_options_scanner and

  import billy_health work without installing the package.

  - Run every test in an isolated temporary CWD so that

    output/health_report_YYYY-MM-DD.json writes never touch the real

      workspace.

      - Stub network calls by default. Tests that need a custom AV response

        override requests.get themselves.

        - Never expose AV_API_KEY: tests use a dummy value or unset it.

        """

from __future__ import annotations

import os

import sys

import pathlib

import pytest

REPOROOT = pathlib.Path(__file__).resolve().parent.parent

if str(REPOROOT) not in sys.path:

      sys.path.insert(0, str(REPOROOT))

@pytest.fixture(autouse=True)

def isolatedcwd(tmp_path, monkeypatch):

      """Run each test in its own tmp directory.

          This guarantees that any output/health_report_*.json write

              happens under tmp_path and not in the real repo.

                  """

    monkeypatch.chdir(tmp_path)

    yield tmp_path

@pytest.fixture(autouse=True)

def cleanenv(monkeypatch):

      """Ensure tests do not leak real secrets and start from a clean env.

          Unset AV_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID so individual

              tests can opt in to dummy values via monkeypatch.setenv().

                  """

    for var in ("AV_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):

              monkeypatch.delenv(var, raising=False)

    yield

@pytest.fixture(autouse=True)

def resetscanner_counters():

      """If billy_options_scanner is already imported, reset its AV

          counters between tests so quota state never bleeds across cases.

              We do not import the scanner here; we only touch it if a previous

                  test imported it. This avoids forcing every health-only test to

                      import the scanner.

                          """

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

      """Make requests.get return a healthy AV GLOBAL_QUOTE response.

          Patches requests.get in the billy_health module's namespace so

              that only the AV probe and the Barchart reachability probe are

                  affected. Any test using this fixture should also stub

                      _probe_barchart if it does not want a Barchart 'ok' result via

                          this same fake.

                              """

    import billy_health

    def _get(url, params=None, timeout=None, headers=None):

              return _FakeResponse(200, {"Global Quote": {"05. price": "123.45"}})

    monkeypatch.setattr(billy_health.requests, "get", _get)

    return _get

@pytest.fixture

def fake_av_rate_limited(monkeypatch):

      """Make AV respond with a rate-limit/informational payload."""

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
