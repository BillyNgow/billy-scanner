"""validate_av_key() schema + status tests (Milestone 1).

These tests pin down the approved Milestone 1 contract for

billy_health.validate_av_key():

- The returned dict contains all approved schema fields.

- av_connectivity correctly classifies missing_key / ok /

  rate_limited / http_error cases.

  - av_probe_calls correctly reflects whether an HTTP attempt was made.

  - ISO timestamps end with 'Z' (UTC).

  - av_free_limit defaults to 25 when the scanner module is not loaded.

  - The AV_API_KEY value never appears in the returned dict. The

    secret name ("AV_API_KEY") may appear in human-readable detail

      strings; that is allowed by design.

      """

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

      """Pin yfinance/Barchart probes to 'ok' so AV-focused tests are

          not affected by the local test environment.

              """

    monkeypatch.setattr(billy_health, "probeyfinance", lambda: "ok")

    monkeypatch.setattr(

        billy_health, "probebarchart", lambda timeout=8.0: "ok"

    )

def test_missing_key_returns_missing_key_status(monkeypatch):

      stub_optional_probes(monkeypatch)

    # Sentinel value used here would never appear in the report,

    # because we pass an empty key. We still check it never leaks.

    sentinel = "S3CRET-DO-NOT-LEAK"

    report = billy_health.validate_av_key(api_key="")

    assert APPROVED_FIELDS.issubset(report.keys())

    assert report["av_key_configured"] is False

    assert report["av_connectivity"] == "missing_key"

    assert report["av_probe_calls"] == 0

    assert report["av_total_estimated_calls"] == (

        report["av_probe_calls"] + report["av_scanner_calls"]

    )

    # Secret VALUE must never appear in the report. The secret NAME

    # ("AV_API_KEY") is allowed to appear in human-readable details.

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

    # Dummy key value never leaks into the report.

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

def test_av_free_limit_default(monkeypatch, fake_av_ok):

      """When billy_options_scanner is not in sys.modules,

          av_free_limit must fall back to the documented default of 25.

              We save and restore sys.modules['billy_options_scanner'] so that

                  other tests (and test collection) are not affected by ordering.

                      """

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

        api_key="DUMMY", scanner_mode="validate-config"

    )

    assert report["scanner_mode"] == "validate-config"

def test_api_key_value_never_in_report(monkeypatch, fake_av_ok):

      """The actual AV_API_KEY VALUE must never appear in the report.

          Note: the secret NAME 'AV_API_KEY' is allowed to appear (e.g. in

              human-readable detail messages). Only the secret VALUE is forbidden.

                  """

    stub_optional_probes(monkeypatch)

    secret = "S3CRET-DO-NOT-LEAK"

    report = billy_health.validate_av_key(api_key=secret)

    assert secret not in str(report)

    for v in report.values():

              assert secret not in str(v)
      
