"""CLI dispatch tests for billy_options_scanner.main() (Milestone 1).

These tests verify:

- validate-config calls the health-check path and writes a report.
- validate-config exits 0 only when Alpha Vantage connectivity is ok.
- the actual AV_API_KEY value is never printed.
- scan dispatches to run().
- no subcommand defaults to run().
- invalid subcommands raise SystemExit via argparse.
"""

from __future__ import annotations

import datetime
import os

import pytest

import billy_health
import billy_options_scanner as scanner


def _stub_optional_probes(monkeypatch):
    """Pin optional probes so CLI tests focus on command dispatch."""
    monkeypatch.setattr(billy_health, "_probe_yfinance", lambda: "ok")
    monkeypatch.setattr(
        billy_health, "_probe_barchart", lambda timeout=8.0: "ok"
    )


def test_validate_config_exit_zero_on_ok(monkeypatch, fake_av_ok):
    _stub_optional_probes(monkeypatch)
    monkeypatch.setenv("AV_API_KEY", "DUMMY")
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    rc = scanner.main(["validate-config"])
    assert rc == 0


def test_validate_config_exit_nonzero_on_missing_key(monkeypatch):
    _stub_optional_probes(monkeypatch)
    # AV_API_KEY is unset by tests/conftest.py.
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    rc = scanner.main(["validate-config"])
    assert rc != 0


def test_validate_config_exit_nonzero_on_rate_limit(
    monkeypatch, fake_av_rate_limited
):
    _stub_optional_probes(monkeypatch)
    monkeypatch.setenv("AV_API_KEY", "DUMMY")
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    rc = scanner.main(["validate-config"])
    assert rc != 0


def test_validate_config_does_not_print_secret(
    monkeypatch, capsys, fake_av_ok
):
    _stub_optional_probes(monkeypatch)
    secret = "S3CRET-DO-NOT-LEAK"
    monkeypatch.setenv("AV_API_KEY", secret)
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    scanner.main(["validate-config"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert secret not in combined


def test_validate_config_writes_health_report(monkeypatch, fake_av_ok):
    import billy_health
    import billy_options_scanner as scanner

    def fake_validate_av_key(api_key=None, timeout=15.0, scanner_mode="cli"):
        """Always return a minimal OK health report and consume 1 AV probe call."""
        return {
            "generated_at_utc": "2026-05-28T00:00:00Z",
            "probed_at_utc": "2026-05-28T00:00:00Z",
            "scanner_mode": scanner_mode,
            "av_key_configured": True,
            "av_connectivity": "ok",
            "av_detail": "Alpha Vantage probe OK",
            "av_probe_calls": 1,
            "av_scanner_calls": 0,
            "av_total_estimated_calls": 1,
            "av_free_limit": 25,
            "yfinance_status": "ok",
            "barchart_reachability": "ok",
            "telegram_status": "missing",
        }

    monkeypatch.setattr(billy_health, "validate_av_key", fake_validate_av_key)
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    _stub_optional_probes(monkeypatch)
    monkeypatch.setenv("AV_API_KEY", "DUMMY")
    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)
    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)
    rc = scanner.main(["validate-config"])
    assert rc == 0
    today = datetime.date.today().isoformat()
    path = os.path.join("output", "health_report_" + today + ".json")
    assert os.path.exists(path)


def test_scan_subcommand_dispatches_to_run(monkeypatch):
    called = {"n": 0}

    def fake_run():
        called["n"] += 1

    monkeypatch.setattr(scanner, "run", fake_run)
    rc = scanner.main(["scan"])
    assert rc == 0
    assert called["n"] == 1


def test_default_subcommand_dispatches_to_run(monkeypatch):
    called = {"n": 0}

    def fake_run():
        called["n"] += 1

    monkeypatch.setattr(scanner, "run", fake_run)
    rc = scanner.main([])
    assert rc == 0
    assert called["n"] == 1


def test_unknown_subcommand_raises_systemexit():
    with pytest.raises(SystemExit):
        scanner.main(["definitely-not-a-command"])
