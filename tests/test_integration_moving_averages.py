"""Integration tests for moving-average wrapper replacement.

Exactly 8 focused tests.

All tests are mocked - no live yfinance calls.
"""

import unittest
from unittest.mock import patch

from provider_result import DataType, Provider, ProviderResult, QualityLabel


def ma_result(ticker, value=None, ok=True, quality=QualityLabel.VERIFIED, error=""):
    """Build a ProviderResult for mocked moving-average wrapper calls."""
    return ProviderResult(
        provider=Provider.YFINANCE,
        data_type=DataType.MARKET_TREND,
        symbol=ticker,
        ok=ok,
        quality=quality,
        value=value,
        source_label="yfinance",
        fetched_at_utc="2026-05-28T20:30:00Z",
        error=error,
    )


def full_ma(price=450.0, ma20=449.0, ma50=448.0, ma200=447.0):
    return {
        "price": price,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
    }


class TestMarketTrendMovingAverageWrapper(unittest.TestCase):
    @patch("provider_wrappers.wrap_moving_averages")
    def test_market_trend_full_history_equivalence(self, mock_wrap):
        from billy_options_scanner import check_market_trend

        def fake_wrap(ticker):
            return ma_result(
                ticker,
                full_ma(price=450.0, ma20=449.0, ma50=448.0, ma200=447.0),
            )

        mock_wrap.side_effect = fake_wrap

        status, reason = check_market_trend()

        self.assertEqual(status, "BULLISH")
        self.assertIsInstance(reason, str)
        self.assertGreaterEqual(mock_wrap.call_count, 2)

    @patch("provider_wrappers.wrap_moving_averages")
    def test_market_trend_short_history_ma200_none_equivalence(self, mock_wrap):
        from billy_options_scanner import check_market_trend

        def fake_wrap(ticker):
            return ma_result(
                ticker,
                full_ma(price=450.0, ma20=449.0, ma50=448.0, ma200=None),
            )

        mock_wrap.side_effect = fake_wrap

        status, reason = check_market_trend()

        self.assertEqual(status, "BULLISH")
        self.assertIsInstance(reason, str)
        self.assertGreaterEqual(mock_wrap.call_count, 2)

    @patch("provider_wrappers.wrap_moving_averages")
    def test_market_trend_insufficient_history_unknown(self, mock_wrap):
        from billy_options_scanner import check_market_trend

        mock_wrap.return_value = ma_result(
            "SPY",
            value=None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="Insufficient price history",
        )

        status, reason = check_market_trend()

        self.assertEqual(status, "UNKNOWN")
        self.assertIn("unavailable", reason.lower())

    @patch("provider_wrappers.get_moving_averages")
    @patch("provider_wrappers.wrap_moving_averages")
    def test_market_trend_uses_wrapper_not_direct_function(
        self, mock_wrap, mock_direct
    ):
        from billy_options_scanner import check_market_trend

        def fake_wrap(ticker):
            return ma_result(
                ticker,
                full_ma(price=450.0, ma20=449.0, ma50=448.0, ma200=447.0),
            )

        mock_wrap.side_effect = fake_wrap

        status, _reason = check_market_trend()

        self.assertEqual(status, "BULLISH")
        self.assertEqual(mock_wrap.call_count, 2)
        mock_direct.assert_not_called()


class TestTickerTrendMovingAverageWrapper(unittest.TestCase):
    @patch("provider_wrappers.wrap_moving_averages")
    def test_ticker_trend_full_history_equivalence(self, mock_wrap):
        from billy_options_scanner import check_ticker_trend

        mock_wrap.return_value = ma_result(
            "AAPL",
            full_ma(price=150.0, ma20=149.0, ma50=148.0, ma200=147.0),
        )

        status, detail = check_ticker_trend("AAPL", 150.0)

        self.assertEqual(status, "BULLISH")
        self.assertIsInstance(detail, str)

    @patch("provider_wrappers.wrap_moving_averages")
    def test_ticker_trend_ma200_none_equivalence(self, mock_wrap):
        from billy_options_scanner import check_ticker_trend

        mock_wrap.return_value = ma_result(
            "AAPL",
            full_ma(price=150.0, ma20=149.0, ma50=148.0, ma200=None),
        )

        status, detail = check_ticker_trend("AAPL", 150.0)

        self.assertEqual(status, "BULLISH")
        self.assertIsInstance(detail, str)

    @patch("provider_wrappers.wrap_moving_averages")
    def test_ticker_trend_missing_data_unknown(self, mock_wrap):
        from billy_options_scanner import check_ticker_trend

        mock_wrap.return_value = ma_result(
            "AAPL",
            value=None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="Insufficient price history",
        )

        status, detail = check_ticker_trend("AAPL", 150.0)

        self.assertEqual(status, "UNKNOWN")
        self.assertIn("moving averages", detail.lower())

    @patch("provider_wrappers.get_moving_averages")
    @patch("provider_wrappers.wrap_moving_averages")
    def test_ticker_trend_uses_wrapper_not_direct_function(
        self, mock_wrap, mock_direct
    ):
        from billy_options_scanner import check_ticker_trend

        mock_wrap.return_value = ma_result(
            "AAPL",
            full_ma(price=150.0, ma20=149.0, ma50=148.0, ma200=147.0),
        )

        status, _detail = check_ticker_trend("AAPL", 150.0)

        self.assertEqual(status, "BULLISH")
        mock_wrap.assert_called_once_with("AAPL")
        mock_direct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
