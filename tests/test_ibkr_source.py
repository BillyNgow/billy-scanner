
"""Tests that IBKR is accepted as a TAKE_IT-eligible IVR source."""
import billy_options_scanner as scanner

def test_take_it_ivr_sources_contains_ibkr():
    assert "IBKR" in scanner.TAKE_IT_IVR_SOURCES

def test_take_it_ivr_sources_contains_barchart():
    assert "Barchart" in scanner.TAKE_IT_IVR_SOURCES

def test_yfinance_estimated_not_in_take_it_sources():
    assert "yfinance-estimated" not in scanner.TAKE_IT_IVR_SOURCES

def test_fmt_summary_mentions_ibkr():
    results = []
    out = scanner.fmt_summary(results, 14.5, "BULLISH")
    assert "IBKR" in out or "Barchart" in out
