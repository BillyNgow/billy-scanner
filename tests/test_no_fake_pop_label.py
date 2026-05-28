"""Tests that fake PoP label is replaced with credit_width_proxy."""

from __future__ import annotations

import billy_options_scanner as scanner


def test_calc_metrics_returns_credit_width_proxy_not_pop():
    m = scanner.calc_metrics(credit=1.50, width=5, contracts=1)

    assert "credit_width_proxy" in m
    assert "pop" not in m
    assert m["credit_width_proxy"] == 0.30


def test_calc_metrics_proxy_is_clamped():
    high = scanner.calc_metrics(credit=10.0, width=5, contracts=1)
    assert high["credit_width_proxy"] == 1.0

    zero = scanner.calc_metrics(credit=1.0, width=0, contracts=1)
    assert zero["credit_width_proxy"] == 0.0


def test_journal_fields_have_credit_width_proxy_not_pop():
    assert "credit_width_proxy" in scanner.JOURNAL_FIELDS
    assert "pop" not in scanner.JOURNAL_FIELDS


def test_journal_row_emits_credit_width_proxy():
    r = {
        "ticker": "AAPL",
        "verdict": "TAKE_IT",
        "credit_width_proxy": 0.30,
    }

    row = scanner._journal_row(r)

    assert "credit_width_proxy" in row
    assert row["credit_width_proxy"] == 0.30
    assert "pop" not in row


def test_fmt_trade_emits_credit_width_proxy_and_no_pop_label():
    r = {
        "ticker": "AAPL",
        "verdict": "TAKE_IT",
        "category": "NORMAL",
        "data_quality": "VERIFIED",
        "reason": "",
        "expiry": "Jun 19 2026",
        "dte": 23,
        "short_strike": 180.0,
        "long_strike": 175.0,
        "delta": 0.30,
        "credit": 1.50,
        "np": 140.0,
        "np_rm": 616.0,
        "nl": 360.0,
        "nl_rm": 1584.0,
        "risk_pct": 1.4,
        "risk_warn": "OK: Within 2% rule (1.4% of account)",
        "be": 178.5,
        "credit_width_proxy": 0.30,
        "open_interest": 1234,
        "bid_ask": 0.10,
        "ivr": 55,
        "ivr_source": "Barchart",
        "ivr_label": "Strong (>=50) - full size",
        "trend": "BULLISH",
        "earnings": "ETF - no earnings",
        "earnings_status": "ETF",
        "options_src": "AV",
        "credit_source": "AV",
        "price_quality": "BID_ASK_MID",
    }

    out = scanner.fmt_trade(r)

    assert "Credit/width proxy: 0.30 (not PoP)" in out
    assert "PoP:" not in out
    assert "PoP " not in out
