"""Unit tests for ibkr_provider.py.

The IBKR integration had zero prior test coverage. These tests verify:
- Contract registry management
- Snapshot/history response parsing
- IV and IVR scaling logic (the most likely source of silent value bugs)
- Moving average computation from price history bars
- HTTP error handling in _call_ibkr_tool
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import ibkr_provider
from provider_result import QualityLabel


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_contract_registry():
    ibkr_provider._CONTRACT_REGISTRY.clear()
    yield
    ibkr_provider._CONTRACT_REGISTRY.clear()


# ── contract registry ─────────────────────────────────────────────────────────

def test_preload_populates_registry():
    result = ibkr_provider.preload_ibkr_contracts(["SPY", "QQQ"])
    assert "SPY" in result
    assert "QQQ" in result
    assert result["SPY"]["contract_id"] == ibkr_provider._KNOWN_CONTRACTS["SPY"]["contract_id"]


def test_preload_returns_all_known_contracts():
    result = ibkr_provider.preload_ibkr_contracts(["SPY"])
    assert len(result) == len(ibkr_provider._KNOWN_CONTRACTS)


def test_preload_warns_for_unknown_tickers(capsys):
    ibkr_provider.preload_ibkr_contracts(["UNKNOWN_XYZ"])
    captured = capsys.readouterr()
    assert "UNKNOWN_XYZ" in captured.out


def test_get_contract_returns_known_ticker():
    contract = ibkr_provider._get_contract("SPY")
    assert contract is not None
    assert "contract_id" in contract
    assert "exchange" in contract


def test_get_contract_returns_none_for_unknown():
    contract = ibkr_provider._get_contract("TOTALLY_UNKNOWN_ZZZ")
    assert contract is None


# ── _call_ibkr_tool ───────────────────────────────────────────────────────────

def test_call_ibkr_tool_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = ibkr_provider._call_ibkr_tool("get_price_snapshot", {})
    assert result is None


def test_call_ibkr_tool_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {}
    with patch("ibkr_provider._requests.post", return_value=mock_resp):
        result = ibkr_provider._call_ibkr_tool("get_price_snapshot", {"contract_id": 1, "exchange": "ARCA", "market_data_names": []})
    assert result is None


def test_call_ibkr_tool_parses_mcp_tool_result_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    payload = {"last": {"price": 450.0}}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [
            {"type": "mcp_tool_result", "content": [{"text": json.dumps(payload)}]}
        ]
    }
    with patch("ibkr_provider._requests.post", return_value=mock_resp):
        result = ibkr_provider._call_ibkr_tool("get_price_snapshot", {"contract_id": 1, "exchange": "ARCA", "market_data_names": []})
    assert result == payload


def test_call_ibkr_tool_falls_back_to_text_block_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    payload = {"last": {"price": 123.4}}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(payload)}]
    }
    with patch("ibkr_provider._requests.post", return_value=mock_resp):
        result = ibkr_provider._call_ibkr_tool("get_price_snapshot", {"contract_id": 1, "exchange": "ARCA", "market_data_names": []})
    assert result == payload


def test_call_ibkr_tool_returns_none_when_no_parseable_content(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"content": [{"type": "text", "text": "not json"}]}
    with patch("ibkr_provider._requests.post", return_value=mock_resp):
        result = ibkr_provider._call_ibkr_tool("get_price_snapshot", {"contract_id": 1, "exchange": "ARCA", "market_data_names": []})
    assert result is None


# ── ibkr_get_price_and_iv ─────────────────────────────────────────────────────

def test_get_price_and_iv_returns_missing_for_unknown_ticker():
    result = ibkr_provider.ibkr_get_price_and_iv("UNKNOWN_ZZZ")
    assert result.ok is False
    assert result.quality == QualityLabel.MISSING


def test_get_price_and_iv_returns_missing_when_tool_returns_none():
    with patch("ibkr_provider._call_ibkr_tool", return_value=None):
        result = ibkr_provider.ibkr_get_price_and_iv("SPY")
    assert result.ok is False
    assert result.quality == QualityLabel.MISSING


def test_get_price_and_iv_parses_snapshot_correctly():
    snap = {
        "last": {"price": 450.25},
        "implied-vol-underlying": {"annual_iv": 0.22},
        "historical-vol": {"annual_pct": 0.18},
        "implied-volatility-percentile": {"high_52w": 0.55},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("SPY")
    assert result.ok is True
    assert result.value["price"] == 450.25
    assert result.value["iv"] == 22.0     # 0.22 * 100
    assert result.value["hv"] == 18.0     # 0.18 * 100
    assert result.value["ivr"] == 55.0    # 0.55 * 100
    assert result.value["ivr_source"] == "IBKR"


def test_get_price_and_iv_iv_scaling_decimal_values():
    # annual_iv < 5 → multiply by 100
    snap = {
        "last": {"price": 100.0},
        "implied-vol-underlying": {"annual_iv": 0.30},
        "historical-vol": {"annual_pct": 0.25},
        "implied-volatility-percentile": {"high_52w": 0.60},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("AAPL")
    assert result.value["iv"] == 30.0
    assert result.value["hv"] == 25.0


def test_get_price_and_iv_iv_scaling_large_values():
    # annual_iv >= 5 → use as-is (already a percentage)
    snap = {
        "last": {"price": 100.0},
        "implied-vol-underlying": {"annual_iv": 30.0},
        "historical-vol": {"annual_pct": 25.0},
        "implied-volatility-percentile": {"high_52w": 0.60},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("AAPL")
    assert result.value["iv"] == 30.0
    assert result.value["hv"] == 25.0


def test_get_price_and_iv_ivr_prefers_52w_over_26w():
    snap = {
        "last": {"price": 100.0},
        "implied-vol-underlying": {"annual_iv": 0.25},
        "historical-vol": {"annual_pct": 0.20},
        "implied-volatility-percentile": {"high_52w": 0.70, "high_26w": 0.40},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("AAPL")
    assert result.value["ivr"] == 70.0    # 52w wins


def test_get_price_and_iv_ivr_falls_back_to_26w_when_no_52w():
    snap = {
        "last": {"price": 100.0},
        "implied-vol-underlying": {"annual_iv": 0.25},
        "historical-vol": {"annual_pct": 0.20},
        "implied-volatility-percentile": {"high_26w": 0.45},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("AAPL")
    assert result.value["ivr"] == 45.0    # 26w fallback


def test_get_price_and_iv_returns_missing_when_no_price_in_snapshot():
    snap = {
        "last": {},
        "prior-close": {},
        "implied-vol-underlying": {},
        "historical-vol": {},
        "implied-volatility-percentile": {},
    }
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_price_and_iv("SPY")
    assert result.ok is False
    assert result.quality == QualityLabel.MISSING


# ── ibkr_get_vix ──────────────────────────────────────────────────────────────

def test_ibkr_get_vix_parses_snapshot():
    snap = {"last": {"price": 18.5}}
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_vix()
    assert result.ok is True
    assert result.value == 18.5


def test_ibkr_get_vix_returns_missing_when_tool_fails():
    with patch("ibkr_provider._call_ibkr_tool", return_value=None):
        result = ibkr_provider.ibkr_get_vix()
    assert result.ok is False


def test_ibkr_get_vix_uses_prior_close_fallback():
    snap = {"last": {}, "prior-close": {"price": 20.0}}
    with patch("ibkr_provider._call_ibkr_tool", return_value=snap):
        result = ibkr_provider.ibkr_get_vix()
    assert result.ok is True
    assert result.value == 20.0


# ── ibkr_get_moving_averages ──────────────────────────────────────────────────

def _make_bars(n: int, start_price: float = 400.0, step: float = 0.1) -> list[dict]:
    return [{"close": round(start_price + i * step, 2)} for i in range(n)]


def test_ibkr_get_moving_averages_computes_ma20_ma50():
    bars = _make_bars(210, start_price=400.0, step=0.1)
    with patch("ibkr_provider._call_ibkr_tool", return_value={"bars": bars}):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is True
    closes = [b["close"] for b in bars]
    assert result.value["price"] == round(closes[-1], 2)
    assert result.value["ma20"] == round(sum(closes[-20:]) / 20, 2)
    assert result.value["ma50"] == round(sum(closes[-50:]) / 50, 2)


def test_ibkr_get_moving_averages_computes_ma200_with_enough_data():
    bars = _make_bars(210)
    with patch("ibkr_provider._call_ibkr_tool", return_value={"bars": bars}):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is True
    assert result.value["ma200"] is not None


def test_ibkr_get_moving_averages_ma200_none_with_insufficient_history():
    bars = _make_bars(100)
    with patch("ibkr_provider._call_ibkr_tool", return_value={"bars": bars}):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is True
    assert result.value["ma200"] is None


def test_ibkr_get_moving_averages_missing_when_too_few_bars():
    bars = _make_bars(30)    # < 50 required
    with patch("ibkr_provider._call_ibkr_tool", return_value={"bars": bars}):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is False
    assert result.quality == QualityLabel.MISSING


def test_ibkr_get_moving_averages_missing_when_tool_returns_none():
    with patch("ibkr_provider._call_ibkr_tool", return_value=None):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is False


def test_ibkr_get_moving_averages_missing_for_unknown_ticker():
    result = ibkr_provider.ibkr_get_moving_averages("UNKNOWN_ZZZ")
    assert result.ok is False
    assert result.quality == QualityLabel.MISSING


def test_ibkr_get_moving_averages_accepts_data_key_fallback():
    # Some responses use "data" instead of "bars"
    bars = _make_bars(60)
    with patch("ibkr_provider._call_ibkr_tool", return_value={"data": bars}):
        result = ibkr_provider.ibkr_get_moving_averages("SPY")
    assert result.ok is True


# ── ibkr_get_market_prices ────────────────────────────────────────────────────

def test_ibkr_get_market_prices_returns_price_and_pct():
    def _fake_tool(tool_name, tool_input, max_tokens=2000):
        ticker_map = {756733: "SPY", 320227571: "QQQ"}
        ticker = ticker_map.get(tool_input["contract_id"])
        if ticker == "SPY":
            return {"last": {"price": 450.0}, "change": {"pct_change": 0.5}}
        if ticker == "QQQ":
            return {"last": {"price": 380.0}, "change": {"pct_change": -0.3}}
        return None

    with patch("ibkr_provider._call_ibkr_tool", side_effect=_fake_tool):
        result = ibkr_provider.ibkr_get_market_prices(["SPY", "QQQ"])

    assert "SPY" in result
    assert result["SPY"]["price"] == 450.0
    assert result["SPY"]["pct"] == 0.5
    assert result["QQQ"]["price"] == 380.0


def test_ibkr_get_market_prices_skips_unknown_tickers():
    result = ibkr_provider.ibkr_get_market_prices(["UNKNOWN_ZZZ"])
    assert "UNKNOWN_ZZZ" not in result


def test_ibkr_get_market_prices_skips_failed_snapshots():
    with patch("ibkr_provider._call_ibkr_tool", return_value=None):
        result = ibkr_provider.ibkr_get_market_prices(["SPY"])
    assert result == {}
