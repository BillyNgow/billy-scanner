"""Health report persistence + freshness/reuse tests (Milestone 1).

Covers:

- write_health_report() creates output/health_report_YYYY-MM-DD.json

  and never persists the AV_API_KEY value.

  - load_fresh_report() correctly returns / rejects reports based on the

    embedded probed_at_utc timestamp (the 10-minute freshness window).

    - The scanner's AV quota accounting is consistent with billy_health:

        AV total = AV_PRE_PROBE_CALLS + AV_CALL_COUNT

        - ensurefresh_health_report() carries av_probe_calls forward into

          the scanner's AV_PRE_PROBE_CALLS on BOTH the reused-report branch

            AND the fresh-probe branch.

            - finalizehealth_report_after_scan() rewrites today's report so the

              uploaded artifact reflects the scanner's actual AV usage.

              """

from __future__ import annotations

import datetime

import json

import os

import billy_health

def stub_optional_probes(monkeypatch):

      monkeypatch.setattr(billy_health, "probeyfinance", lambda: "ok")

    monkeypatch.setattr(

        billy_health, "probebarchart", lambda timeout=8.0: "ok"

    )

# -------------------------------------------------------------------

# write_health_report()

# -------------------------------------------------------------------

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

    # Secret VALUE is never persisted to disk.

    with open(path, "r") as f:

              raw = f.read()

    assert "DUMMY" not in raw

def test_write_health_report_never_persists_secret_value(

    monkeypatch, fake_av_ok

):

      stub_optional_probes(monkeypatch)

    secret = "S3CRET-DO-NOT-LEAK"

    report = billy_health.validate_av_key(api_key=secret)

    path = billy_health.write_health_report(report)

    with open(path, "r") as f:

              raw = f.read()

    assert secret not in raw

# -------------------------------------------------------------------

# load_fresh_report() - happy path + rejection paths

# -------------------------------------------------------------------

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

    # Backdate probed_at_utc by 1 hour.

    stale = datetime.datetime.utcnow() - datetime.timedelta(hours=1)

    report["probed_at_utc"] = stale.replace(microsecond=0).isoformat() + "Z"

    billy_health.write_health_report(report)

    assert billy_health.load_fresh_report(max_age_seconds=600) is None

def test_load_fresh_report_rejects_unparseable_probed_at(

    monkeypatch, fake_av_ok

):

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

      """Reports older than 10 minutes must NOT be reused. Reports

          under 10 minutes MUST be reused.

              """

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

# -------------------------------------------------------------------

# AV quota accounting + scanner integration

# -------------------------------------------------------------------

def test_quota_accounting_after_probe(monkeypatch, fake_av_ok):

      """When the scanner module is loaded, the probe must bump

          AV_PRE_PROBE_CALLS by exactly 1, and av_total_estimated_calls

              must equal AV_PRE_PROBE_CALLS + AV_CALL_COUNT.

                  """

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

      """When ensurefresh_health_report reuses a fresh report, the

          scanner's AV_PRE_PROBE_CALLS MUST be set from the report so the

              quota guard accounts for the validate-config probe.

                  """

    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    # Simulate validate-config having produced a fresh report with

    # av_probe_calls = 1.

    report = billy_health.validate_av_key(api_key="DUMMY")

    report["av_probe_calls"] = 1

    billy_health.write_health_report(report)

    # Reset the scanner counter and call the reuse path.

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)

    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)

    scanner._ensure_fresh_health_report(scanner_mode="scan")

    assert scanner.AV_PRE_PROBE_CALLS == 1

def test_fresh_probe_sets_av_pre_probe_calls_when_no_cached_report(

    monkeypatch, fake_av_ok

):

      """When there is no cached health report on disk, the fresh-probe

          branch of ensurefresh_health_report() must still set the

              scanner's AV_PRE_PROBE_CALLS from the report it just produced.

                  This guards against the case where billy_options_scanner is loaded

                      as main (not under sys.modules["billy_options_scanner"]), so

                          billy_health._bump_scanner_pre_probe_counter() cannot reach it.

                              """

    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    # No existing report file (_isolated_cwd gives a clean tmp dir).

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 0, raising=False)

    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 0, raising=False)

    monkeypatch.setenv("AV_API_KEY", "DUMMY")

    scanner._ensure_fresh_health_report(scanner_mode="scan")

    assert scanner.AV_PRE_PROBE_CALLS == 1

def test_finalize_rewrites_av_scanner_calls(monkeypatch, fake_av_ok):

      """After the scan, finalizehealth_report_after_scan() must

          rewrite today's report so av_scanner_calls reflects AV_CALL_COUNT.

              """

    stub_optional_probes(monkeypatch)

    import billy_options_scanner as scanner

    # Seed a fresh report (as validate-config would).

    report = billy_health.validate_av_key(api_key="DUMMY")

    report["av_probe_calls"] = 1

    report["av_scanner_calls"] = 0

    report["av_total_estimated_calls"] = 1

    billy_health.write_health_report(report)

    # Pretend the scan used 3 AV calls.

    monkeypatch.setattr(scanner, "AV_PRE_PROBE_CALLS", 1, raising=False)

    monkeypatch.setattr(scanner, "AV_CALL_COUNT", 3, raising=False)

    scanner._finalize_health_report_after_scan()

    with open(billy_health.today_report_path(), "r") as f:

              final = json.load(f)

    assert final["av_probe_calls"] == 1

    assert final["av_scanner_calls"] == 3

    assert final["av_total_estimated_calls"] == 4

    assert final["av_free_limit"] == scanner.AV_FREE_LIMIT
