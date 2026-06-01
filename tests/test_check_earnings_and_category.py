"""Unit tests for check_earnings().

The earnings gate is one of the key trade-safety checks — yet it had
zero direct tests. These tests cover the ETF shortcut, the 14-day
EARNINGS_BUFFER boundary, the "unknown" fallback, and the yfinance
calendar parse path.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

import billy_options_scanner as scanner


# ── ETF shortcut ──────────────────────────────────────────────────────────────

def test_etf_always_safe_no_yfinance_call():
    with patch("billy_options_scanner.yf") as mock_yf:
        safe, days, date_str, status = scanner.check_earnings("SPY")
    mock_yf.Ticker.assert_not_called()
    assert safe is True
    assert days == 999
    assert status == "ETF"
    assert "ETF" in date_str


def test_all_etf_list_members_return_etf_status():
    for ticker in scanner.ETF_LIST:
        safe, days, date_str, status = scanner.check_earnings(ticker)
        assert safe is True, f"{ticker} should always be safe (ETF)"
        assert status == "ETF"


# ── yfinance calendar — confirmed earnings ────────────────────────────────────

def _make_ticker_with_earnings(days_away: int) -> MagicMock:
    earnings_date = datetime.date.today() + datetime.timedelta(days=days_away)
    mock_tk = MagicMock()
    mock_tk.calendar = {"Earnings Date": [pd.Timestamp(earnings_date)]}
    return mock_tk


def test_earnings_far_away_is_safe():
    mock_tk = _make_ticker_with_earnings(days_away=30)
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, date_str, status = scanner.check_earnings("AAPL")
    assert safe is True
    assert days == 30
    assert status == "CONFIRMED"


def test_earnings_within_buffer_is_not_safe():
    mock_tk = _make_ticker_with_earnings(days_away=10)
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, date_str, status = scanner.check_earnings("AAPL")
    assert safe is False
    assert days == 10
    assert status == "CONFIRMED"


def test_earnings_exactly_at_buffer_is_not_safe():
    # days > EARNINGS_BUFFER(14) → safe; days = 14 → NOT safe
    mock_tk = _make_ticker_with_earnings(days_away=scanner.EARNINGS_BUFFER)
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, _, _ = scanner.check_earnings("AAPL")
    assert safe is False


def test_earnings_one_day_past_buffer_is_safe():
    mock_tk = _make_ticker_with_earnings(days_away=scanner.EARNINGS_BUFFER + 1)
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, _, _ = scanner.check_earnings("AAPL")
    assert safe is True


def test_earnings_date_string_formatted_correctly():
    mock_tk = _make_ticker_with_earnings(days_away=30)
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        _, _, date_str, _ = scanner.check_earnings("AAPL")
    # Expected format: "Jun 01 2026" — a 3-letter month abbreviation
    parts = date_str.split()
    assert len(parts) == 3
    assert len(parts[0]) == 3   # month abbrev
    assert parts[1].isdigit()   # day
    assert len(parts[2]) == 4   # 4-digit year


# ── yfinance calendar — unknown / error fallback ──────────────────────────────

def test_unknown_calendar_returns_safe_fallback():
    mock_tk = MagicMock()
    mock_tk.calendar = None
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, date_str, status = scanner.check_earnings("AAPL")
    assert safe is True
    assert days == 999
    assert status == "UNKNOWN"
    assert date_str == "Unknown"


def test_empty_earnings_date_list_returns_unknown():
    mock_tk = MagicMock()
    mock_tk.calendar = {"Earnings Date": []}
    with patch("billy_options_scanner.yf.Ticker", return_value=mock_tk):
        safe, days, date_str, status = scanner.check_earnings("AAPL")
    assert safe is True
    assert status == "UNKNOWN"


def test_yfinance_exception_returns_unknown_fallback():
    mock_tk = MagicMock()
    mock_tk.calendar = property(lambda self: (_ for _ in ()).throw(RuntimeError("network")))
    with patch("billy_options_scanner.yf.Ticker", side_effect=RuntimeError("network")):
        safe, days, date_str, status = scanner.check_earnings("AAPL")
    assert safe is True
    assert status == "UNKNOWN"
