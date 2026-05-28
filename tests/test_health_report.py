"""Health report persistence, freshness, and scanner quota accounting tests."""

from __future__ import annotations

import datetime
import json
import os

import billy_health


def stub_optional_probes(monkeypatch):
    monkeypatch.setattr(billy_health, "_probe_yfinance", lambda: "ok")
    monkeypatch.setattr(
        billy_health,
        "_probe_barchart",
        lambda timeout=8.0: "ok",
    )


def test_write_health_report_creates_file(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")
    path = billy_health.write_health_report(report)

    assert os.path.exists(path)

    today = datetime.date.today().isoformat()
    assert path.endswith("health_report_" + today + ".json")

    with open(path, "r") as f:
        loaded = json.load(f)

    assert loaded == report

    with open(path, "r") as f:
        raw = f.read()

    assert "DUMMY" not in raw


def test_write_health_report_never_persists_secret_value(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    secret = "S3CRET-DO-NOT-LEAK"
    report = billy_health.validate_av_key(api_key=secret)
    path = billy_health.write_health_report(report)

    with open(path, "r") as f:
        raw = f.read()

    assert secret not in raw


def test_load_fresh_report_returns_none_when_missing():
    assert billy_health.load_fresh_report() is None


def test_load_fresh_report_returns_report_when_fresh(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")
    billy_health.write_health_report(report)

    loaded = billy_health.load_fresh_report(max_age_seconds=600)

    assert loaded is not None
    assert loaded["av_connectivity"] == "ok"


def test_load_fresh_report_rejects_stale_probed_at(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")
    stale = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    report["probed_at_utc"] = stale.replace(microsecond=0).isoformat() + "Z"

    billy_health.write_health_report(report)

    assert billy_health.load_fresh_report(max_age_seconds=600) is None


def test_load_fresh_report_rejects_unparseable_probed_at(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")
    report["probed_at_utc"] = "not-a-timestamp"

    billy_health.write_health_report(report)

    assert billy_health.load_fresh_report(max_age_seconds=600) is None


def test_load_fresh_report_rejects_missing_probed_at(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")
    del report["probed_at_utc"]

    billy_health.write_health_report(report)

    assert billy_health.load_fresh_report(max_age_seconds=600) is None


def test_fresh_window_is_under_ten_minutes(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    report = billy_health.validate_av_key(api_key="DUMMY")

    just_over = datetime.datetime.utcnow() - datetime.timedelta(seconds=601)
    report["probed_at_utc"] = just_over.replace(microsecond=0).isoformat() + "Z"
    billy_health.write_health_report(report)
    assert billy_health.load_fresh_report(max_age_seconds=600) is None

    just_under = datetime.datetime.utcnow() - datetime.timedelta(seconds=10)
    report["probed_at_utc"] = just_under.replace(microsecond=0).isoformat() + "Z"
    billy_health.write_health_report(report)
    assert billy_health.load_fresh_report(max_age_seconds=600) is not None


def test_quota_accounting_after_probe(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)

    report = billy_health.validate_av_key(api_key="DUMMY")

    assert scanner.AV_PRE_PROBE_CALLS == 1
    assert report["av_probe_calls"] == 1
    assert report["av_scanner_calls"] == scanner.AV_CALL_COUNT
    assert report["av_total_estimated_calls"] == (
        scanner.AV_PRE_PROBE_CALLS + scanner.AV_CALL_COUNT
    )
    assert report["av_free_limit"] == scanner.AV_FREE_LIMIT


def test_reuse_carries_av_probe_calls_into_scanner(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    report = billy_health.validate_av_key(api_key="DUMMY")
    report["av_probe_calls"] = 1
    billy_health.write_health_report(report)

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)

    scanner._ensure_fresh_health_report(scanner_mode="scan")

    assert scanner.AV_PRE_PROBE_CALLS == 1


def test_fresh_probe_sets_av_pre_probe_calls_when_no_cached_report(
    monkeypatch,
    fake_av_ok,
):
    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    monkeypatch.setenv("AV_API_KEY", "DUMMY")

    scanner._ensure_fresh_health_report(scanner_mode="scan")

    assert scanner.AV_PRE_PROBE_CALLS == 1


def test_finalize_rewrites_av_scanner_calls(monkeypatch, fake_av_ok):
    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    report = billy_health.validate_av_key(api_key="DUMMY")
    report["av_probe_calls"] = 1
    report["av_scanner_calls"] = 0
    report["av_total_estimated_calls"] = 1
    billy_health.write_health_report(report)

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 1, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 3, raising=False)

    scanner._finalize_health_report_after_scan()

    with open(billy_health.today_report_path(), "r") as f:
        final = json.load(f)

    assert final["av_probe_calls"] == 1
    assert final["av_scanner_calls"] == 3
    assert final["av_total_estimated_calls"] == 4
    assert final["av_free_limit"] == scanner.AV_FREE_LIMIT
