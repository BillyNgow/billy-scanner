"""validate_av_key() schema and status tests."""

from __future__ import annotations

import sys

import billy_health

APPROVED_FIELDS = {
    "generated_at_utc",
    "probed_at_utc",
    "scanner_mode",
    "av_key_configured",
    "av_connectivity",
    "av_detail",
    "av_probe_calls",
    "av_scanner_calls",
    "av_total_estimated_calls",
    "av_free_limit",
    "yfinance_status",
    "barchart_reachability",
    "telegram_status",
}


def stub_optional_probes(monkeypatch):
    """Pin optional probes so AV-focused tests are deterministic."""
    monkeypatch.setattr(billy_health, "_probe_yfinance", lambda: "ok")
    monkeypatch.setattr(
        billy_health,
        "_probe_barchart",
        lambda timeout=8.0: "ok",
    )


def test_missing_key_returns_missing_key_status(monkeypatch):
    stub_optional_probes(monkeypatch)

    sentinel = "S3CRET-DO-NOT-LEAK"
    report = billy_health.validate_av_key(api_key="")

    assert APPROVED_FIELDS.issubset(report.keys())
    assert report["av_key_configured"] is False
    assert report["av_connectivity"] == "missing_key"
    assert report["av_probe_calls"] == 0
    assert report["av_total_estimated_calls"] == (
        report["av_probe_calls"] + report["av_scanner_calls"]
    )
    assert sentinel not in str(report)


def test_ok_response_returns_ok(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")

    assert APPROVED_FIELDS.issubset(report.keys())
    assert report["av_key_configured"] is True
    assert report["av_connectivity"] == "ok"
    assert report["av_probe_calls"] == 1
    assert report["av_total_estimated_calls"] == (
        report["av_probe_calls"] + report["av_scanner_calls"]
    )
    assert report["scanner_mode"] in ("cli", "validate-config", "scan")
    assert "DUMMY" not in str(report)


def test_rate_limited_response(monkeypatch, fake_av_rate_limited):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")

    assert report["av_connectivity"] == "rate_limited"
    assert report["av_probe_calls"] == 1
    assert "DUMMY" not in str(report)


def test_http_error_response(monkeypatch, fake_av_http_error):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")

    assert report["av_connectivity"] == "http_error"
    assert "503" in report["av_detail"]
    assert report["av_probe_calls"] == 1


def test_iso_timestamps_are_utc_z(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")

    assert isinstance(report["generated_at_utc"], str)
    assert isinstance(report["probed_at_utc"], str)
    assert report["generated_at_utc"].endswith("Z")
    assert report["probed_at_utc"].endswith("Z")


def test_av_free_limit_default_when_scanner_not_loaded(monkeypatch, fake_av_ok):
    """When scanner is not loaded, av_free_limit falls back to 25."""
    stub_optional_probes(monkeypatch)

    saved = sys.modules.pop("billy_options_scanner", None)

    try:
        report = billy_health.validate_av_key(api_key="DUMMY")
        assert report["av_free_limit"] == 25
    finally:
        if saved is not None:
            sys.modules["billy_options_scanner"] = saved


def test_scanner_mode_is_passed_through(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(
        api_key="DUMMY",
        scanner_mode="validate-config",
    )

    assert report["scanner_mode"] == "validate-config"


def test_api_key_value_never_in_report(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    secret = "S3CRET-DO-NOT-LEAK"
    report = billy_health.validate_av_key(api_key=secret)

    assert secret not in str(report)
    for value in report.values():
        assert secret not in str(value)
