"""
billy_health.py - Milestone 1

Alpha Vantage health probe + environment health report for the Billy
scanner.

Public surface:

    validate_av_key(api_key=None, timeout=15.0, scanner_mode="cli")
    write_health_report(report, path=None)
    load_fresh_report(max_age_seconds=600)
    today_report_path(today=None)

No secret value is ever printed, logged, or written to disk.
The actual AV_API_KEY value never appears in the report.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import requests

AV_BASE = "https://www.alphavantage.co/query"
AV_PROBE_SYMBOL = "SPY"
AV_FREE_LIMIT_DEFAULT = 25
HEALTH_REPORT_DIR = "output"
BARCHART_PROBE_URL = "https://www.barchart.com/stocks/quotes/SPY/overview"

# Maximum age, in seconds, of an existing report that may be reused.
FRESH_MAX_AGE_SECONDS = 600


def _utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def _iso_z(dt):
    return dt.isoformat() + "Z"


def today_report_path(today=None):
    today = today or datetime.date.today()
    return os.path.join(
        HEALTH_REPORT_DIR,
        "health_report_" + today.isoformat() + ".json",
    )


def _bump_scanner_pre_probe_counter(n=1):
    """If billy_options_scanner is imported, increment its
    AV_PRE_PROBE_CALLS counter so quota accounting stays correct.
    """
    if n <= 0:
        return

    mod = sys.modules.get("billy_options_scanner")
    if mod is None:
        return

    try:
        current = int(getattr(mod, "AV_PRE_PROBE_CALLS", 0) or 0)
        setattr(mod, "AV_PRE_PROBE_CALLS", current + n)
    except Exception:
        # Accounting must never break the health check.
        pass


def _read_scanner_counters():
    """Return av_probe_calls, av_scanner_calls, av_free_limit."""
    mod = sys.modules.get("billy_options_scanner")
    if mod is None:
        return 0, 0, AV_FREE_LIMIT_DEFAULT

    try:
        probe = int(getattr(mod, "AV_PRE_PROBE_CALLS", 0) or 0)
        scanner = int(getattr(mod, "AV_CALL_COUNT", 0) or 0)
        limit = int(
            getattr(mod, "AV_FREE_LIMIT", AV_FREE_LIMIT_DEFAULT)
            or AV_FREE_LIMIT_DEFAULT
        )
        return probe, scanner, limit
    except Exception:
        return 0, 0, AV_FREE_LIMIT_DEFAULT


def _probe_av(api_key, timeout):
    """Single Alpha Vantage probe.

    Returns:
        (av_connectivity, av_detail)
    """
    if not api_key:
        return "missing_key", "AV_API_KEY is not set"

    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": AV_PROBE_SYMBOL,
        "apikey": api_key,
    }

    try:
        resp = requests.get(AV_BASE, params=params, timeout=timeout)
    except Exception as e:
        _bump_scanner_pre_probe_counter(1)
        return "exception", "request failed: " + type(e).__name__

    _bump_scanner_pre_probe_counter(1)

    if resp.status_code != 200:
        return "http_error", "HTTP " + str(resp.status_code)

    try:
        data = resp.json()
    except Exception:
        return "exception", "non-JSON response"

    if "Note" in data or "Information" in data:
        return "rate_limited", "Alpha Vantage rate limit / informational response"

    quote = data.get("Global Quote") or {}
    if not quote or not quote.get("05. price"):
        return "empty_quote", "Empty Global Quote"

    return "ok", "Alpha Vantage probe OK"


def _probe_yfinance():
    """Returns ok, import_error, or unavailable."""
    try:
        import yfinance as yf
    except Exception:
        return "import_error"

    try:
        yf.Ticker(AV_PROBE_SYMBOL)
        return "ok"
    except Exception:
        return "unavailable"


def _probe_barchart(timeout=8.0):
    """Returns ok, http_error, or unreachable."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"}
        resp = requests.get(BARCHART_PROBE_URL, headers=headers, timeout=timeout)
    except Exception:
        return "unreachable"

    if resp.status_code == 200:
        return "ok"

    return "http_error"


def _telegram_status():
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if token and chat_id:
        return "configured"

    if token or chat_id:
        return "partial"

    return "missing"


def validate_av_key(api_key=None, timeout=15.0, scanner_mode="cli"):
    """Run health checks and return a safe health report.

    This function never returns or logs the actual AV_API_KEY value.
    """
    if api_key is None:
        api_key = os.environ.get("AV_API_KEY", "")

    av_key_configured = bool(api_key)

    probed_at = _utc_now()

    av_connectivity, av_detail = _probe_av(api_key, timeout=timeout)
    yfinance_status = _probe_yfinance()
    barchart_reachability = _probe_barchart()
    telegram_status = _telegram_status()

    av_probe_calls, av_scanner_calls, av_free_limit = _read_scanner_counters()

    # If scanner module is absent, use local accounting.
    if sys.modules.get("billy_options_scanner") is None:
        av_probe_calls = 0 if av_connectivity == "missing_key" else 1

    report = {
        "generated_at_utc": _iso_z(_utc_now()),
        "probed_at_utc": _iso_z(probed_at),
        "scanner_mode": scanner_mode,
        "av_key_configured": av_key_configured,
        "av_connectivity": av_connectivity,
        "av_detail": av_detail,
        "av_probe_calls": int(av_probe_calls),
        "av_scanner_calls": int(av_scanner_calls),
        "av_total_estimated_calls": int(av_probe_calls) + int(av_scanner_calls),
        "av_free_limit": int(av_free_limit),
        "yfinance_status": yfinance_status,
        "barchart_reachability": barchart_reachability,
        "telegram_status": telegram_status,
    }

    return report


def write_health_report(report, path=None):
    """Write health report to output/health_report_YYYY-MM-DD.json."""
    os.makedirs(HEALTH_REPORT_DIR, exist_ok=True)

    output_path = path or today_report_path()

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    return output_path


def _parse_iso_z(value):
    if not isinstance(value, str) or not value:
        return None

    try:
        if value.endswith("Z"):
            return datetime.datetime.fromisoformat(value[:-1])
        return datetime.datetime.fromisoformat(value)
    except Exception:
        return None


def load_fresh_report(max_age_seconds=FRESH_MAX_AGE_SECONDS):
    """Load today's health report only if probed_at_utc is fresh."""
    path = today_report_path()

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            report = json.load(f)
    except Exception:
        return None

    if not isinstance(report, dict):
        return None

    probed_at = _parse_iso_z(report.get("probed_at_utc", ""))
    if probed_at is None:
        return None

    age_seconds = (_utc_now() - probed_at).total_seconds()

    if age_seconds < 0 or age_seconds >= max_age_seconds:
        return None

    return report