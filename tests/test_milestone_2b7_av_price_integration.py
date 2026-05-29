# Integration tests for Milestone 2B-7 Alpha Vantage price wrapper usage.
# Exactly 9 tests.
# All tests are mocked - no live Alpha Vantage, yfinance, or Barchart calls.

import unittest
from types import SimpleNamespace
from unittest.mock import patch


def provider_result(value, ok=True):
    return SimpleNamespace(ok=ok, value=value)


class TestMilestone2B7AvPriceWrapperIntegration(unittest.TestCase):
    def test_av_price_valid_dict_used_as_primary(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result({"price": 150.0, "prev": 149.0})
            mock_yf.return_value = provider_result(
                {"price": 155.0, "iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 150.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.0)
        self.assertEqual(result["ivr_source"], "Barchart")
        mock_av.assert_called_once_with("AAPL")

    def test_av_price_none_falls_back_to_yfinance(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result(None, ok=False)
            mock_yf.return_value = provider_result(
                {"price": 155.0, "iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 155.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_av_price_exception_falls_back_to_yfinance(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result(None, ok=False)
            mock_yf.return_value = provider_result(
                {"price": 156.0, "iv": 31.0, "hv": 23.0, "ivr": 41.0}
            )
            mock_barchart.return_value = provider_result(46.0)

            result = get_iv_data("MSFT")

        self.assertEqual(result["price"], 156.0)
        self.assertEqual(result["iv"], 31.0)
        self.assertEqual(result["hv"], 23.0)
        self.assertEqual(result["ivr"], 46.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_av_price_missing_price_key_falls_back_to_yfinance(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result(None, ok=False)
            mock_yf.return_value = provider_result(
                {"price": 157.0, "iv": 32.0, "hv": 24.0, "ivr": 42.0}
            )
            mock_barchart.return_value = provider_result(47.0)

            result = get_iv_data("GOOG")

        self.assertEqual(result["price"], 157.0)
        self.assertEqual(result["iv"], 32.0)
        self.assertEqual(result["hv"], 24.0)
        self.assertEqual(result["ivr"], 47.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_av_price_overrides_yfinance_price(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result({"price": 200.0, "prev": 199.0})
            mock_yf.return_value = provider_result(
                {"price": 150.0, "iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            result = get_iv_data("AAPL")

        self.assertEqual(result["price"], 200.0)
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_av_price_none_yf_price_also_none_returns_none(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result(None, ok=False)
            mock_yf.return_value = provider_result(
                {"iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            result = get_iv_data("AAPL")

        self.assertIsNone(result["price"])
        self.assertEqual(result["iv"], 30.0)
        self.assertEqual(result["hv"], 22.0)
        self.assertEqual(result["ivr"], 45.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_price_feeds_into_return_dict_correctly(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result({"price": 188.25, "prev": 187.50})
            mock_yf.return_value = provider_result(
                {"price": 150.0, "iv": 33.0, "hv": 25.0, "ivr": 43.0}
            )
            mock_barchart.return_value = provider_result(48.0)

            result = get_iv_data("NVDA")

        self.assertEqual(result["price"], 188.25)
        self.assertEqual(result["iv"], 33.0)
        self.assertEqual(result["hv"], 25.0)
        self.assertEqual(result["ivr"], 48.0)
        self.assertEqual(result["ivr_source"], "Barchart")

    def test_return_dict_shape_unchanged(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result({"price": 150.0, "prev": 149.0})
            mock_yf.return_value = provider_result(
                {"price": 155.0, "iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            result = get_iv_data("AAPL")

        self.assertEqual(
            set(result.keys()),
            {"price", "iv", "hv", "ivr", "ivr_source"},
        )

    def test_barchart_and_yf_wrappers_still_called(self):
        from billy_options_scanner import get_iv_data

        with patch("provider_wrappers.wrap_av_price") as mock_av, \
             patch("provider_wrappers.wrap_yf_iv_data") as mock_yf, \
             patch("provider_wrappers.wrap_barchart_ivr") as mock_barchart:
            mock_av.return_value = provider_result({"price": 150.0, "prev": 149.0})
            mock_yf.return_value = provider_result(
                {"price": 155.0, "iv": 30.0, "hv": 22.0, "ivr": 40.0}
            )
            mock_barchart.return_value = provider_result(45.0)

            get_iv_data("AAPL")

        mock_av.assert_called_once_with("AAPL")
        mock_yf.assert_called_once_with("AAPL")
        mock_barchart.assert_called_once_with("AAPL")


if __name__ == "__main__":
    unittest.main()
