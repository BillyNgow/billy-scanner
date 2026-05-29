"""Integration tests for Milestone 2B-5 yfinance IV wrapper usage.

Exactly 9 tests.

All tests are mocked - no live yfinance, Alpha Vantage, or Barchart calls.
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


class TestMilestone2B5YfIvWrapperIntegration(unittest.TestCase):
    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_full_yfinance_dict_all_fields_extracted(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 65.0, "samples": 10}
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 150.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 65.0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")
        mock_wrap_yf.assert_called_once_with("AAPL")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_partial_price_hv_no_iv_preserved(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result({"price": 100.0, "hv": 18.0})

        result = get_iv_data("XYZ")

        self.assertEqual(result["price"], 100.0)
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 18.0)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_partial_price_only_preserved(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result({"price": 125.0})

        result = get_iv_data("GOOG")

        self.assertEqual(result["price"], 125.0)
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 0)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_partial_hv_only_preserved(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result({"hv": 21.5})

        result = get_iv_data("MSFT")

        self.assertIsNone(result["price"])
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 21.5)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_empty_dict_safe(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {},
            ok=False,
            quality=QualityLabel.MISSING,
            error="get_iv_yfinance returned empty",
        )

        result = get_iv_data("SPY")

        self.assertIsNone(result["price"])
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 0)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_none_return_safe(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result(
            None,
            ok=False,
            quality=QualityLabel.MISSING,
            error="get_iv_yfinance returned None",
        )

        result = get_iv_data("QQQ")

        self.assertIsNone(result["price"])
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 0)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_exception_safe(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result(
            None,
            ok=False,
            quality=QualityLabel.ERROR,
            error="Network timeout",
        )

        result = get_iv_data("TSLA")

        self.assertIsNone(result["price"])
        self.assertEqual(result["iv"], 0)
        self.assertEqual(result["hv"], 0)
        self.assertEqual(result["ivr"], 0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_barchart_ivr_priority_preserved(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = None
        mock_barchart.return_value = 72.0
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 65.0, "samples": 10}
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 150.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 72.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    @patch("provider_wrappers.get_ivr_barchart")
    @patch("billy_options_scanner.av_get_price")
    @patch("provider_wrappers.wrap_yf_iv_data")
    def test_integration_av_price_priority_over_yfinance_price(
        self, mock_wrap_yf, mock_av_price, mock_barchart
    ):
        from billy_options_scanner import get_iv_data

        mock_av_price.return_value = {"price": 200.0, "prev": 199.0}
        mock_barchart.return_value = None
        mock_wrap_yf.return_value = yf_result(
            {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 65.0, "samples": 10}
        )

        result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 200.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 65.0)
        self.assertEqual(result["ivr_source"], "yfinance-estimated")


if __name__ == "__main__":
    unittest.main()
