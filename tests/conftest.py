from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add the repository root to the Python path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))


@pytest.fixture(autouse=True)
def isolated_cwd(monkeypatch, tmp_path):
    """Isolate each test to a temporary directory."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def _probe_yfinance(monkeypatch):
    """Patch yfinance.download to avoid live data calls."""
    def mock_download(*args, **kwargs):
        import pandas as pd
        return pd.DataFrame()

    import yfinance
    monkeypatch.setattr(yfinance, "download", mock_download)


@pytest.fixture
def _probe_barchart(monkeypatch):
    """Patch requests to avoid live API calls."""
    def mock_get(*args, **kwargs):
        class MockResponse:
            status_code = 200
            text = ""
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "get", mock_get)


@pytest.fixture
def fake_av_ok(monkeypatch):
    """Fixture for valid AV API responses."""
    def mock_post(*args, **kwargs):
        class MockResponse:
            status_code = 200
            text = '{"data": "ok"}'
            def json(self):
                return {"data": "ok"}
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)
    return mock_post


@pytest.fixture
def fake_av_rate_limited(monkeypatch):
    """Fixture for rate-limited AV API responses."""
    def mock_post(*args, **kwargs):
        class MockResponse:
            status_code = 429
            text = '{"error": "rate limited"}'
            def json(self):
                return {"error": "rate limited"}
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)
    return mock_post
