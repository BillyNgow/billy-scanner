"""Tests for provider wrapper functions.

Milestone 2B-2: Verify that wrappers correctly convert existing scanner
functions to ProviderResult contracts without changing scanner behavior.

Test count: 12 focused tests.
"""

from unittest.mock import patch

from provider_wrappers import (
    wrap_av_price,
    wrap_av_options_chain,
    wrap_barchart_ivr,
    wrap_yf_iv_data,
    wrap_moving_averages,
    wrap_vix,
)
from provider_result import Provider, DataType, QualityLabel


# --- AV Price Tests (3 tests) ---


def test_wrap_av_price_success():
    """av_get_price returns dict -> wrap_av_price returns VERIFIED ProviderResult."""
    with patch("provider_wrappers.av_get_price") as mock_av:
        mock_av.return_value = {"price": 450.50, "prev": 449.00}
        result = wrap_av_price("SPY")

        assert result.ok is True
        assert result.quality == QualityLabel.VERIFIED
        assert result.provider == Provider.ALPHA_VANTAGE
        assert result.data_type == DataType.PRICE
        assert result.value == {"price": 450.50, "prev": 449.00}
        assert result.symbol == "SPY"
        assert result.source_label == "AlphaVantage"
        assert result.fetched_at_utc != ""


def test_wrap_av_price_missing():
    """av_get_price returns None -> wrap_av_price returns MISSING ProviderResult."""
    with patch("provider_wrappers.av_get_price") as mock_av:
        mock_av.return_value = None
        result = wrap_av_price("INVALID")

        assert result.ok is False
        assert result.quality == QualityLabel.MISSING
        assert result.error != ""
        assert result.value is None


def test_wrap_av_price_exception():
    """av_get_price raises exception -> wrap_av_price returns ERROR ProviderResult."""
    with patch("provider_wrappers.av_get_price") as mock_av:
        mock_av.side_effect = ValueError("API error")
        result = wrap_av_price("SPY")

        assert result.ok is False
        assert result.quality == QualityLabel.ERROR
        assert "API error" in result.error


# --- AV Options Chain Tests (2 tests) ---


def test_wrap_av_options_chain_success():
    """av_get_options_chain returns non-empty list -> VERIFIED."""
    with patch("provider_wrappers.av_get_options_chain") as mock_av:
        mock_options = [
            {"type": "put", "strike": 450, "bid": 1.0, "ask": 1.5},
            {"type": "put", "strike": 445, "bid": 0.8, "ask": 1.2},
        ]
        mock_av.return_value = mock_options
        result = wrap_av_options_chain("SPY")

        assert result.ok is True
        assert result.quality == QualityLabel.VERIFIED
        assert result.provider == Provider.ALPHA_VANTAGE
        assert result.data_type == DataType.OPTIONS_CHAIN
        assert len(result.value) == 2


def test_wrap_av_options_chain_missing():
    """av_get_options_chain returns None -> MISSING."""
    with patch("provider_wrappers.av_get_options_chain") as mock_av:
        mock_av.return_value = None
        result = wrap_av_options_chain("SPY")

        assert result.ok is False
        assert result.quality == QualityLabel.MISSING


# --- Barchart IVR Tests (2 tests) ---


def test_wrap_barchart_ivr_success():
    """Barchart scrape returns float -> VERIFIED."""
    with patch("provider_wrappers.get_ivr_barchart") as mock_bc:
        mock_bc.return_value = 65.5
        result = wrap_barchart_ivr("SPY")

        assert result.ok is True
        assert result.quality == QualityLabel.VERIFIED
        assert result.provider == Provider.BARCHART
        assert result.data_type == DataType.IVR
        assert result.value == 65.5
        assert result.source_label == "Barchart"


def test_wrap_barchart_ivr_missing():
    """Barchart scrape returns None -> MISSING."""
    with patch("provider_wrappers.get_ivr_barchart") as mock_bc:
        mock_bc.return_value = None
        result = wrap_barchart_ivr("SPY")

        assert result.ok is False
        assert result.quality == QualityLabel.MISSING
        assert "scrape failed" in result.error.lower()


# --- yfinance IV Data Test (1 test) ---


def test_wrap_yf_iv_data_estimated():
    """yfinance IV data returns dict -> ESTIMATED, not VERIFIED."""
    with patch("provider_wrappers.get_iv_yfinance") as mock_yf:
        mock_yf.return_value = {
            "price": 450.0,
            "iv": 18.5,
            "hv": 17.2,
            "ivr": 55.0,
            "samples": 5,
        }
        result = wrap_yf_iv_data("SPY")

        assert result.ok is True
        assert result.quality == QualityLabel.ESTIMATED
        assert result.provider == Provider.YFINANCE
        assert result.data_type == DataType.IVR
        assert result.value["iv"] == 18.5
        assert result.source_label == "yfinance"


# --- Moving Averages Test (1 test) ---


def test_wrap_moving_averages_success():
    """get_moving_averages returns all MAs -> VERIFIED."""
    with patch("provider_wrappers.get_moving_averages") as mock_ma:
        mock_ma.return_value = {
            "price": 450.0,
            "ma20": 448.5,
            "ma50": 447.0,
            "ma200": 445.0,
        }
        result = wrap_moving_averages("SPY")

        assert result.ok is True
        assert result.quality == QualityLabel.VERIFIED
        assert result.provider == Provider.YFINANCE
        assert result.data_type == DataType.MARKET_TREND
        assert result.value["ma20"] == 448.5
        assert result.source_label == "yfinance"


# --- VIX Test (1 test) ---


def test_wrap_vix_success():
    """get_vix returns float -> VERIFIED."""
    with patch("provider_wrappers.get_vix") as mock_vix:
        mock_vix.return_value = 18.5
        result = wrap_vix()

        assert result.ok is True
        assert result.quality == QualityLabel.VERIFIED
        assert result.provider == Provider.YFINANCE
        assert result.data_type == DataType.VIX
        assert result.value == 18.5
        assert result.symbol == "^VIX"
        assert result.source_label == "yfinance"


# --- Serialization & Shared Tests (2 tests) ---


def test_wrapper_result_serialization():
    """ProviderResult from wrappers can be serialized to dict with no secrets."""
    with patch("provider_wrappers.av_get_price") as mock_av:
        mock_av.return_value = {"price": 100, "prev": 99}
        result = wrap_av_price("TEST")

        data = result.to_dict()
        assert isinstance(data, dict)
        assert data["provider"] == "AlphaVantage"
        assert data["quality"] == "VERIFIED"
        assert "fetched_at_utc" in data
        assert data["fetched_at_utc"] != ""
        assert "T" in data["fetched_at_utc"]
        assert "Z" in data["fetched_at_utc"]

        serialized_str = str(data)
        assert "apikey" not in serialized_str.lower()


def test_wrapper_module_imports():
    """provider_wrappers module imports correctly with all 6 wrappers."""
    import provider_wrappers

    assert hasattr(provider_wrappers, "wrap_av_price")
    assert hasattr(provider_wrappers, "wrap_av_options_chain")
    assert hasattr(provider_wrappers, "wrap_barchart_ivr")
    assert hasattr(provider_wrappers, "wrap_yf_iv_data")
    assert hasattr(provider_wrappers, "wrap_moving_averages")
    assert hasattr(provider_wrappers, "wrap_vix")

    assert callable(provider_wrappers.wrap_av_price)
    assert callable(provider_wrappers.wrap_barchart_ivr)
