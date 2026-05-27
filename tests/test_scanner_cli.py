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
