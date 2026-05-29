"""Integration tests for Milestone 2B-6 Barchart IVR wrapper usage.

Exactly 9 tests.

All tests are mocked - no live Barchart, yfinance, or Alpha Vantage calls.
"""

import unittest
from unittest.mock import patch

from provider_result import DataType, Provider, ProviderResult, QualityLabel


def yf_result(value, ok=True, quality=QualityLabel.ESTIMATED, error=""):
    return ProviderResult(
        provider=Provider.YFINANCE,
        data_type=DataType.IVR,
        symbol="TEST",
        ok=ok,
        quality=quality,
        value=value,
        source_label="yfinance",
        fetched_at_utc="2026-05-29T00:00:00Z",
        error=error,
    )


def barchart_result(value, ok=True, quality=QualityLabel.VERIFIED, error=""):
    return ProviderResult(
        provider=Provider.BARCHART,
        data_type=DataType.IVR,
        symbol="TEST",
        ok=ok,
        quality=quality,
        value=value,
        source_label="Barchart",
        fetched_at_utc="2026-05-29T00:00:00Z",
        error=error,
    )


class TestMilestone2B6BarchartIvrWrapperIntegration(unittest.TestCase):
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_valid_ivr_float_used_as_primary(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(45.5)

        result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 150.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.5)
        self.assertEqual(result["ivr_source"], "Barchart")
        mock_wrap_barchart.assert_called_once_with("AAPL")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_zero_ivr_treated_as_valid(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(0)

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "Barchart")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_none_falls_back_to_yfinance(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(
            None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="Barchart scrape failed or timeout",
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 32.0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_exception_falls_back_to_yfinance(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 28.5, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(
            None,
            ok=False,
            quality=QualityLabel.ERROR,
            error="Network timeout",
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 28.5)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_non_numeric_falls_back_to_yfinance(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 31.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(
            None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="Barchart returned invalid type",
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 31.0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_success_overrides_yfinance_ivr(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(55.0)

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 55.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_barchart_failure_yfinance_ivr_fallback(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 38.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(
            None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="Barchart scrape failed or timeout",
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["ivr"], 38.0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_av_price_priority_over_yfinance_price_unchanged(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = {"price": 200.0, "prev": 199.0}
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(45.5)

        result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 200.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.5)
        self.assertEqual(result["ivr_source"], "Barchart")

    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    @patch("provider_wrappers.wrap_barchart_ivr")
    def test_return_dict_shape_unchanged(
        self, mock_wrap_barchart, mock_wrap_yf, mock_av_price
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 32.0, "samples": 10}
        )
        mock_wrap_barchart.return_value = barchart_result(45.5)

        result = get_iv_data("AAPL")

        self.assertEqual(
            set(result.keys()),
            {"price", "iv", "hv", "ivr", "ivr_source"},
        )


if __name__ == "__main__":
    unittest.main()
