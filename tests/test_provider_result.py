"""Tests for provider_result.py contract.

Milestone 2B-1 only.

These tests verify the provider result contract, serialization, quality labels,
and quality eligibility helpers. They do not touch scanner logic.
"""

from __future__ import annotations

import re

from provider_result import (
    DataType,
    Provider,
    ProviderResult,
    QualityLabel,
    is_take_it_eligible_quality,
    is_verified,
    provider_error,
    provider_estimated,
    provider_missing,
    provider_ok,
    provider_rate_limited,
    provider_stale,
    utc_now_iso,
)


def test_enum_values_are_stable():
    assert Provider.ALPHA_VANTAGE.value == "AlphaVantage"
    assert Provider.YFINANCE.value == "yfinance"
    assert Provider.BARCHART.value == "Barchart"
    assert Provider.UNKNOWN.value == "Unknown"

    assert DataType.PRICE.value == "price"
    assert DataType.OPTIONS_CHAIN.value == "options_chain"
    assert DataType.OPTION_LEG.value == "option_leg"
    assert DataType.IVR.value == "ivr"
    assert DataType.HV.value == "hv"
    assert DataType.EARNINGS.value == "earnings"
    assert DataType.VIX.value == "vix"
    assert DataType.MARKET_TREND.value == "market_trend"

    assert QualityLabel.VERIFIED.value == "VERIFIED"
    assert QualityLabel.ESTIMATED.value == "ESTIMATED"
    assert QualityLabel.MISSING.value == "MISSING"
    assert QualityLabel.STALE.value == "STALE"
    assert QualityLabel.RATE_LIMITED.value == "RATE_LIMITED"
    assert QualityLabel.ERROR.value == "ERROR"


def test_utc_now_iso_returns_utc_z_string():
    value = utc_now_iso()

    assert isinstance(value, str)
    assert value.endswith("Z")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value)


def test_provider_ok_creates_verified_result():
    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
        raw={"price": "123.45"},
        source_label="AV",
        fetched_at_utc="2026-05-28T00:00:00Z",
    )

    assert isinstance(result, ProviderResult)
    assert result.provider == Provider.ALPHA_VANTAGE
    assert result.data_type == DataType.PRICE
    assert result.symbol == "SPY"
    assert result.ok is True
    assert result.quality == QualityLabel.VERIFIED
    assert result.value == 123.45
    assert result.raw == {"price": "123.45"}
    assert result.source_label == "AV"
    assert result.fetched_at_utc == "2026-05-28T00:00:00Z"
    assert result.stale is False
    assert result.staleness_reason == ""
    assert result.error == ""


def test_provider_estimated_creates_estimated_result():
    result = provider_estimated(
        provider=Provider.YFINANCE,
        data_type=DataType.IVR,
        symbol="AAPL",
        value=42,
        source_label="yfinance-estimated",
    )

    assert result.ok is True
    assert result.quality == QualityLabel.ESTIMATED
    assert result.value == 42
    assert result.source_label == "yfinance-estimated"
    assert result.stale is False


def test_provider_missing_creates_missing_result():
    result = provider_missing(
        provider=Provider.BARCHART,
        data_type=DataType.IVR,
        symbol="TSLA",
        error="IVR not found",
    )

    assert result.ok is False
    assert result.quality == QualityLabel.MISSING
    assert result.value is None
    assert result.source_label == "Barchart"
    assert result.error == "IVR not found"


def test_provider_stale_creates_stale_result():
    result = provider_stale(
        provider=Provider.YFINANCE,
        data_type=DataType.OPTIONS_CHAIN,
        symbol="QQQ",
        value={"rows": 10},
        fetched_at_utc="2026-05-26T00:00:00Z",
        staleness_reason="older than max age",
    )

    assert result.ok is False
    assert result.quality == QualityLabel.STALE
    assert result.value == {"rows": 10}
    assert result.stale is True
    assert result.staleness_reason == "older than max age"


def test_provider_rate_limited_creates_rate_limited_result():
    result = provider_rate_limited(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        error="rate limit reached",
    )

    assert result.ok is False
    assert result.quality == QualityLabel.RATE_LIMITED
    assert result.error == "rate limit reached"
    assert result.stale is False


def test_provider_error_creates_error_result():
    result = provider_error(
        provider=Provider.UNKNOWN,
        data_type=DataType.EARNINGS,
        symbol="META",
        error="request failed",
    )

    assert result.ok is False
    assert result.quality == QualityLabel.ERROR
    assert result.error == "request failed"
    assert result.source_label == "Unknown"


def test_to_dict_serializes_enum_values_as_strings():
    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
        source_label="AV",
        fetched_at_utc="2026-05-28T00:00:00Z",
    )

    data = result.to_dict()

    assert data["provider"] == "AlphaVantage"
    assert data["data_type"] == "price"
    assert data["quality"] == "VERIFIED"
    assert data["symbol"] == "SPY"
    assert data["ok"] is True
    assert data["value"] == 123.45
    assert data["source_label"] == "AV"


def test_default_source_label_uses_provider_value():
    result = provider_ok(
        provider=Provider.BARCHART,
        data_type=DataType.IVR,
        symbol="SPY",
        value=55,
    )

    assert result.source_label == "Barchart"


def test_is_verified_true_only_for_verified_ok_non_stale_result():
    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
    )

    assert is_verified(result) is True


def test_is_verified_false_for_estimated_result():
    result = provider_estimated(
        provider=Provider.YFINANCE,
        data_type=DataType.IVR,
        symbol="AAPL",
        value=42,
    )

    assert is_verified(result) is False


def test_take_it_eligible_quality_true_only_for_verified_result():
    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
    )

    assert is_take_it_eligible_quality(result) is True


def test_take_it_eligible_quality_false_for_missing_stale_rate_limited_and_error():
    results = [
        provider_missing(
            provider=Provider.BARCHART,
            data_type=DataType.IVR,
            symbol="SPY",
            error="missing",
        ),
        provider_stale(
            provider=Provider.YFINANCE,
            data_type=DataType.OPTIONS_CHAIN,
            symbol="SPY",
            staleness_reason="old data",
        ),
        provider_rate_limited(
            provider=Provider.ALPHA_VANTAGE,
            data_type=DataType.PRICE,
            symbol="SPY",
            error="rate limited",
        ),
        provider_error(
            provider=Provider.UNKNOWN,
            data_type=DataType.EARNINGS,
            symbol="SPY",
            error="request failed",
        ),
    ]

    for result in results:
        assert is_take_it_eligible_quality(result) is False


def test_take_it_eligible_quality_false_for_estimated_result():
    result = provider_estimated(
        provider=Provider.YFINANCE,
        data_type=DataType.IVR,
        symbol="SPY",
        value=50,
    )

    assert is_take_it_eligible_quality(result) is False


def test_result_does_not_leak_secret_when_secret_is_not_passed():
    secret = "S3CRET-DO-NOT-LEAK"

    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
        raw={"safe": "metadata only"},
    )

    assert secret not in str(result)
    assert secret not in str(result.to_dict())


def test_provider_result_is_frozen():
    result = provider_ok(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.PRICE,
        symbol="SPY",
        value=123.45,
    )

    try:
        result.value = 999
        changed = True
    except Exception:
        changed = False

    assert changed is False
