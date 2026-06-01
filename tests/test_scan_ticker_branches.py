"""Unit tests for scan_ticker() decision branches.

scan_ticker() has ~12 distinct exit paths; previously only the happy-path
data flow was tested via integration mocks. These tests exercise each
branch in isolation by controlling the mocked inputs.

All external I/O is mocked — no live market data calls.
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import billy_options_scanner as scanner
from provider_result import DataType, Provider, ProviderResult, QualityLabel


# ── shared fixtures ───────────────────────────────────────────────────────────

def _iv_data(price=450.0, iv=25.0, hv=20.0, ivr=55.0, ivr_source="Barchart"):
    return {"price": price, "iv": iv, "hv": hv, "ivr": ivr, "ivr_source": ivr_source}


def _av_chain_result(ok=True):
    return ProviderResult(
        provider=Provider.ALPHA_VANTAGE,
        data_type=DataType.OPTIONS_CHAIN,
        symbol="TEST",
        ok=ok,
        quality=QualityLabel.VERIFIED if ok else QualityLabel.MISSING,
        value=[{"type": "put", "strike": "430"}] if ok else None,
        source_label="AlphaVantage",
        fetched_at_utc="2026-06-01T00:00:00Z",
        error="" if ok else "No data",
    )


def _good_spread(short_strike=430.0, delta=0.28, oi=250, ba_width=0.20,
                 short_quality="BID_ASK_MID", long_quality="BID_ASK_MID",
                 short_price=3.50, long_price=1.50):
    expiry = (datetime.date.today() + datetime.timedelta(days=45)).isoformat()
    return {
        "expiry": expiry,
        "dte": 45,
        "short_strike": short_strike,
        "long_strike": short_strike - scanner.SPREAD_WIDTH,
        "short_pq": {"price": short_price, "quality": short_quality,
                     "bid": (short_price - 0.05) if short_price is not None else None,
                     "ask": (short_price + 0.05) if short_price is not None else None},
        "long_pq": {"price": long_price, "quality": long_quality,
                    "bid": long_price - 0.05, "ask": long_price + 0.05},
        "delta": delta,
        "delta_method": "AV Greeks",
        "oi": oi,
        "ba_width": ba_width,
        "iv": 25.0,
        "source": "AlphaVantage",
    }


@contextmanager
def _patched_scan(
    iv_data=None,
    trend=("BULLISH", "Above all MAs"),
    earnings=(True, 90, "Sep 01 2026", "CONFIRMED"),
    spread=None,
    av_chain_ok=True,
):
    """Patch all external calls inside scan_ticker and yield."""
    spread_val = spread if spread is not None else _good_spread()
    with (
        patch("billy_options_scanner.get_iv_data", return_value=iv_data or _iv_data()),
        patch("billy_options_scanner.check_ticker_trend", return_value=trend),
        patch("billy_options_scanner.check_earnings", return_value=earnings),
        patch("provider_wrappers.wrap_av_options_chain", return_value=_av_chain_result(av_chain_ok)),
        patch("billy_options_scanner.av_find_spread_legs", return_value=spread_val),
    ):
        yield


# ── early-exit SKIP branches ──────────────────────────────────────────────────

def test_skip_when_no_price_data():
    with _patched_scan(iv_data={"price": None, "iv": 0, "hv": 0, "ivr": 0, "ivr_source": "yfinance-estimated"}):
        r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] == "SKIP"
    assert "price" in r["reason"].lower()


def test_skip_when_market_is_bearish():
    with _patched_scan():
        r = scanner.scan_ticker("SPY", vix=15, market_trend_status="BEARISH")
    assert r["verdict"] == "SKIP"
    assert "bearish" in r["reason"].lower()


def test_skip_when_ivr_below_minimum():
    with _patched_scan(iv_data=_iv_data(ivr=20.0, ivr_source="Barchart")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "SKIP"
    assert "IVR" in r["reason"]


def test_skip_when_high_risk_ticker_below_hr_min_ivr():
    # HIGH_RISK tickers need IVR >= HR_MIN_IV_RANK (50), not MIN_IV_RANK (30)
    with _patched_scan(iv_data=_iv_data(ivr=40.0, ivr_source="Barchart")):
        r = scanner.scan_ticker("NVDA", vix=15)
    assert r["verdict"] == "SKIP"
    assert "IVR" in r["reason"]


def test_skip_when_vix_at_or_above_30():
    with _patched_scan():
        r = scanner.scan_ticker("SPY", vix=30)
    assert r["verdict"] == "SKIP"
    assert "VIX" in r["reason"]


def test_skip_when_earnings_too_close_and_confirmed():
    with _patched_scan(earnings=(False, 8, "Jun 09 2026", "CONFIRMED")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "SKIP"
    assert "8d" in r["reason"] or "Earnings" in r["reason"]


def test_earnings_unknown_does_not_skip_early():
    # UNKNOWN earnings should not cause early skip — it causes a late downgrade
    with _patched_scan(earnings=(True, 999, "Unknown", "UNKNOWN")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] != "SKIP" or "earnings" not in r["reason"].lower()


# ── options chain / spread SKIP branches ─────────────────────────────────────

def test_skip_when_open_interest_too_low():
    with _patched_scan(spread=_good_spread(oi=10)):
        r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] == "SKIP"
    assert "OI" in r["reason"] or "interest" in r["reason"].lower()


def test_skip_when_bid_ask_too_wide():
    with _patched_scan(spread=_good_spread(ba_width=0.60)):
        r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] == "SKIP"
    assert "B/A" in r["reason"] or "Spread" in r["reason"]


def test_skip_when_delta_too_high():
    with _patched_scan(spread=_good_spread(delta=0.40)):
        r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] == "SKIP"
    assert "Delta" in r["reason"] or "delta" in r["reason"]


def test_skip_when_short_price_missing():
    spread = _good_spread(short_price=None, short_quality="MISSING")
    spread["short_pq"]["price"] = None
    with _patched_scan(spread=spread):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "SKIP"


def test_skip_when_credit_below_minimum():
    # MIN_CREDIT_RATIO=0.33, SPREAD_WIDTH=5 → min_credit=1.65
    spread = _good_spread(short_price=1.50, long_price=0.10)  # credit=1.40 < 1.65
    with _patched_scan(spread=spread):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "SKIP"
    assert "Credit" in r["reason"] or "credit" in r["reason"]


def test_skip_when_invalid_credit_short_lte_long():
    spread = _good_spread(short_price=1.00, long_price=1.50)  # short <= long → invalid
    with _patched_scan(spread=spread):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "SKIP"


# ── MANUAL_CHECK (downgrade) branches ────────────────────────────────────────

def test_downgrade_when_ivr_source_not_confirmed():
    with _patched_scan(iv_data=_iv_data(ivr=60.0, ivr_source="yfinance-estimated")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "MANUAL_CHECK"
    assert "IVR" in r["reason"] or "estimated" in r["reason"].lower()


def test_downgrade_when_market_trend_unknown():
    with _patched_scan():
        r = scanner.scan_ticker("SPY", vix=15, market_trend_status="UNKNOWN")
    assert r["verdict"] == "MANUAL_CHECK"
    assert "trend" in r["reason"].lower() or "Market" in r["reason"]


def test_downgrade_when_ticker_trend_bearish():
    with _patched_scan(trend=("BEARISH", "Below 200MA")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "MANUAL_CHECK"
    assert "200MA" in r["reason"] or "BEARISH" in r["reason"] or "trend" in r["reason"].lower()


def test_downgrade_when_ticker_trend_caution():
    with _patched_scan(trend=("CAUTION", "Below 50MA")):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "MANUAL_CHECK"
    assert "50MA" in r["reason"] or "CAUTION" in r["reason"] or "trend" in r["reason"].lower()


def test_downgrade_when_long_leg_price_missing():
    spread = _good_spread()
    spread["long_pq"] = {"price": None, "quality": "MISSING", "bid": None, "ask": None}
    with _patched_scan(spread=spread):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "MANUAL_CHECK"
    assert "long" in r["reason"].lower()


def test_downgrade_when_short_price_quality_is_last_only():
    spread = _good_spread(short_quality="LAST_PRICE_ONLY")
    with _patched_scan(spread=spread):
        r = scanner.scan_ticker("AAPL", vix=15)
    assert r["verdict"] == "MANUAL_CHECK"
    assert "LAST_PRICE_ONLY" in r["reason"] or "quality" in r["reason"].lower()


def test_downgrade_when_risk_exceeds_2pct():
    # Force contracts=2 to push nl_usd above 2% threshold:
    # nl_usd = (5-2)*100*2 + fees_2 = 600+6.32 = 606.32 → 2.4%
    with _patched_scan():
        with patch("billy_options_scanner.size_contracts", return_value=2):
            r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] in ("MANUAL_CHECK", "SKIP")
    assert "risk" in r["reason"].lower() or "Risk" in r["reason"]


def test_skip_when_risk_exceeds_3pct():
    # Force contracts=10 to push nl_usd well above 3% threshold:
    # nl_usd = (5-2)*100*10 + fees = 3000+31.6 = 3031.6 → 12.1%
    with _patched_scan():
        with patch("billy_options_scanner.size_contracts", return_value=10):
            r = scanner.scan_ticker("SPY", vix=15)
    assert r["verdict"] == "SKIP"
    assert "3%" in r["reason"] or "risk" in r["reason"].lower() or "Risk" in r["reason"]


# ── TAKE_IT happy path ────────────────────────────────────────────────────────

def test_take_it_when_all_conditions_met():
    # All filters pass → verdict should be TAKE_IT
    with _patched_scan(
        iv_data=_iv_data(ivr=60.0, ivr_source="Barchart"),
        trend=("BULLISH", "Above all MAs"),
        earnings=(True, 90, "Sep 01 2026", "CONFIRMED"),
        spread=_good_spread(delta=0.28, oi=300, ba_width=0.15),
    ):
        r = scanner.scan_ticker("SPY", vix=15, market_trend_status="BULLISH")
    assert r["verdict"] == "TAKE_IT"
    assert r["data_quality"] == "VERIFIED"


def test_take_it_result_contains_credit_and_strikes():
    with _patched_scan(
        iv_data=_iv_data(ivr=60.0, ivr_source="Barchart"),
        trend=("BULLISH", "Above all MAs"),
        earnings=(True, 90, "Sep 01 2026", "CONFIRMED"),
        spread=_good_spread(short_strike=430.0, delta=0.28, oi=300, ba_width=0.15),
    ):
        r = scanner.scan_ticker("SPY", vix=15, market_trend_status="BULLISH")
    assert r["verdict"] == "TAKE_IT"
    assert r["credit"] == round(3.50 - 1.50, 2)
    assert r["short_strike"] == 430.0
    assert r["long_strike"] == 425.0


def test_take_it_sets_breakeven():
    spread = _good_spread(short_strike=430.0, short_price=3.50, long_price=1.50)
    with _patched_scan(
        iv_data=_iv_data(ivr=60.0, ivr_source="Barchart"),
        trend=("BULLISH", "Above all MAs"),
        earnings=(True, 90, "Sep 01 2026", "CONFIRMED"),
        spread=spread,
    ):
        r = scanner.scan_ticker("SPY", vix=15, market_trend_status="BULLISH")
    assert r["verdict"] == "TAKE_IT"
    # be = short_strike - credit
    assert r["be"] == round(430.0 - (3.50 - 1.50), 2)


def test_vix_25_halves_contracts_via_size_mod():
    # vix=27 → size_mod=0.5; contracts should be 1 (floor(500*0.5/300)=floor(250/300)=0 → min 1)
    with _patched_scan(
        iv_data=_iv_data(ivr=60.0, ivr_source="Barchart"),
        trend=("BULLISH", "Above all MAs"),
        earnings=(True, 90, "Sep 01 2026", "CONFIRMED"),
        spread=_good_spread(delta=0.28, oi=300, ba_width=0.15),
    ):
        r = scanner.scan_ticker("SPY", vix=27, market_trend_status="BULLISH")
    # Should still produce a verdict (not VIX-skip) but with reduced size
    assert r["verdict"] in ("TAKE_IT", "MANUAL_CHECK")
    assert "VIXx0.5" in r.get("size_note", "")
