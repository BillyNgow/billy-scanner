"""Tests for Milestone 2B-5 yfinance IV wrapper partial-dict preservation.

Exactly 9 tests.

All tests are mocked - no live yfinance calls.
"""

import unittest
from unittest.mock import patch

from provider_result import QualityLabel
from provider_wrappers import wrap_yf_iv_data


class TestWrapYfIvDataPartialDictFix(unittest.TestCase):
    @patch("provider_wrappers.get_iv_yfinance")
    def test_full_dict_with_iv_is_estimated_and_preserved(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {
            "price": 150.5,
            "iv": 30.2,
            "hv": 28.1,
            "ivr": 65.0,
            "samples": 10,
        }

        result = wrap_yf_iv_data("AAPL")

        self.assertTrue(result.ok)
        self.assertEqual(result.quality, QualityLabel.ESTIMATED)
        self.assertEqual(result.value["price"], 150.5)
        self.assertEqual(result.value["iv"], 30.2)
        self.assertEqual(result.value["hv"], 28.1)
        self.assertEqual(result.value["ivr"], 65.0)

    @patch("provider_wrappers.get_iv_yfinance")
    def test_partial_dict_price_hv_no_iv_is_preserved(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {"price": 100.0, "hv": 18.0}

        result = wrap_yf_iv_data("XYZ")

        self.assertTrue(result.ok)
        self.assertEqual(result.quality, QualityLabel.ESTIMATED)
        self.assertIsNotNone(result.value)
        self.assertEqual(result.value.get("price"), 100.0)
        self.assertEqual(result.value.get("hv"), 18.0)
        self.assertIsNone(result.value.get("iv"))

    @patch("provider_wrappers.get_iv_yfinance")
    def test_partial_dict_price_only_is_preserved(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {"price": 125.0}

        result = wrap_yf_iv_data("GOOG")

        self.assertTrue(result.ok)
        self.assertEqual(result.quality, QualityLabel.ESTIMATED)
        self.assertEqual(result.value.get("price"), 125.0)
        self.assertIsNone(result.value.get("iv"))

    @patch("provider_wrappers.get_iv_yfinance")
    def test_partial_dict_hv_only_is_preserved(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {"hv": 21.5}

        result = wrap_yf_iv_data("MSFT")

        self.assertTrue(result.ok)
        self.assertEqual(result.quality, QualityLabel.ESTIMATED)
        self.assertEqual(result.value.get("hv"), 21.5)
        self.assertIsNone(result.value.get("iv"))

    @patch("provider_wrappers.get_iv_yfinance")
    def test_empty_dict_is_missing(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {}

        result = wrap_yf_iv_data("SPY")

        self.assertFalse(result.ok)
        self.assertEqual(result.quality, QualityLabel.MISSING)
        self.assertIsNone(result.value)

    @patch("provider_wrappers.get_iv_yfinance")
    def test_none_return_is_missing(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = None

        result = wrap_yf_iv_data("QQQ")

        self.assertFalse(result.ok)
        self.assertEqual(result.quality, QualityLabel.MISSING)
        self.assertIsNone(result.value)

    @patch("provider_wrappers.get_iv_yfinance")
    def test_exception_is_error(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.side_effect = Exception("Network timeout")

        result = wrap_yf_iv_data("TSLA")

        self.assertFalse(result.ok)
        self.assertEqual(result.quality, QualityLabel.ERROR)
        self.assertIn("timeout", result.error.lower())

    @patch("provider_wrappers.get_iv_yfinance")
    def test_scanner_style_extraction_full_data(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {
            "price": 150.5,
            "iv": 30.2,
            "hv": 28.1,
            "ivr": 65.0,
            "samples": 10,
        }

        result = wrap_yf_iv_data("AAPL")
        yfd = result.value if result.value else {}

        price = yfd.get("price")
        iv = yfd.get("iv", 0)
        hv = yfd.get("hv", 0)
        ivr = yfd.get("ivr", 0)

        self.assertEqual(price, 150.5)
        self.assertEqual(iv, 30.2)
        self.assertEqual(hv, 28.1)
        self.assertEqual(ivr, 65.0)

    @patch("provider_wrappers.get_iv_yfinance")
    def test_scanner_style_extraction_partial_data_preserves_price(self, mock_get_iv_yfinance):
        mock_get_iv_yfinance.return_value = {"price": 100.0, "hv": 18.0}

        result = wrap_yf_iv_data("XYZ")
        yfd = result.value if result.value else {}

        price = yfd.get("price")
        iv = yfd.get("iv", 0)
        hv = yfd.get("hv", 0)
        ivr = yfd.get("ivr", 0)

        self.assertEqual(price, 100.0)
        self.assertEqual(iv, 0)
        self.assertEqual(hv, 18.0)
        self.assertEqual(ivr, 0)


if __name__ == "__main__":
    unittest.main()
