"""Integration tests for VIX wrapper replacement in scanner.

Exactly 6 focused tests.

All tests are mocked - no live yfinance calls.
"""

import unittest
from unittest.mock import patch

from provider_result import DataType, Provider, ProviderResult, QualityLabel
from provider_wrappers import wrap_vix


class TestVixWrapperExtraction(unittest.TestCase):
    @patch("provider_wrappers.get_vix")
    def test_vix_extraction_success_float(self, mock_get_vix):
        mock_get_vix.return_value = 22.40

        vix_result = wrap_vix()
        vix = vix_result.value if vix_result.ok else None

        self.assertEqual(vix, 22.40)
        self.assertIsInstance(vix, float)
        self.assertTrue(vix_result.ok)
        self.assertEqual(vix_result.quality, QualityLabel.VERIFIED)
        self.assertEqual(vix_result.data_type, DataType.VIX)

    @patch("provider_wrappers.get_vix")
    def test_vix_extraction_missing_none(self, mock_get_vix):
        mock_get_vix.return_value = None

        vix_result = wrap_vix()
        vix = vix_result.value if vix_result.ok else None

        self.assertIsNone(vix)
        self.assertFalse(vix_result.ok)
        self.assertEqual(vix_result.quality, QualityLabel.MISSING)

    @patch("provider_wrappers.get_vix")
    def test_vix_extraction_error_none(self, mock_get_vix):
        mock_get_vix.side_effect = Exception("Network timeout")

        vix_result = wrap_vix()
        vix = vix_result.value if vix_result.ok else None

        self.assertIsNone(vix)
        self.assertFalse(vix_result.ok)
        self.assertEqual(vix_result.quality, QualityLabel.ERROR)
        self.assertIn("Network timeout", vix_result.error)


class TestVixDownstreamBehavior(unittest.TestCase):
    @patch("provider_wrappers.get_vix")
    def test_vix_size_modifier_matches_existing_behavior(self, mock_get_vix):
        from billy_options_scanner import vix_size_modifier

        test_cases = [
            (None, 1.0),
            (12.5, 1.0),
            (22.0, 1.0),
            (25.0, 0.5),
            (28.5, 0.5),
            (30.0, 0.0),
            (35.2, 0.0),
        ]

        for vix_val, expected_mod in test_cases:
            mock_get_vix.return_value = vix_val

            vix_result = wrap_vix()
            vix = vix_result.value if vix_result.ok else None

            self.assertEqual(vix_size_modifier(vix), expected_mod)

    @patch("provider_wrappers.get_vix")
    def test_vix_label_matches_existing_behavior(self, mock_get_vix):
        from billy_options_scanner import vix_label

        test_values = [
            None,
            14.9,
            15.0,
            19.9,
            20.0,
            24.9,
            25.0,
            29.9,
            30.0,
            35.5,
        ]

        for vix_val in test_values:
            mock_get_vix.return_value = vix_val

            direct_label = vix_label(vix_val)

            vix_result = wrap_vix()
            wrapped_vix = vix_result.value if vix_result.ok else None
            wrapped_label = vix_label(wrapped_vix)

            self.assertEqual(wrapped_vix, vix_val)
            self.assertEqual(wrapped_label, direct_label)


class TestRunIntegration(unittest.TestCase):
    @patch("provider_wrappers.wrap_vix")
    @patch("provider_wrappers.get_vix")
    @patch("billy_options_scanner.get_market")
    @patch("billy_options_scanner.check_market_trend")
    @patch("billy_options_scanner.scan_ticker")
    @patch("billy_options_scanner.send_telegram")
    @patch("billy_options_scanner._ensure_fresh_health_report")
    @patch("billy_options_scanner._finalize_health_report_after_scan")
    @patch("billy_options_scanner.write_journal")
    def test_run_uses_wrap_vix_not_direct_get_vix(
        self,
        mock_write_journal,
        mock_finalize_health,
        mock_ensure_health,
        mock_send_telegram,
        mock_scan_ticker,
        mock_market_trend,
        mock_get_market,
        mock_get_vix_direct,
        mock_wrap_vix,
    ):
        import billy_options_scanner

        mock_wrap_vix.return_value = ProviderResult(
            provider=Provider.YFINANCE,
            data_type=DataType.VIX,
            symbol="^VIX",
            ok=True,
            quality=QualityLabel.VERIFIED,
            value=22.40,
            source_label="yfinance",
            fetched_at_utc="2026-05-28T20:30:00Z",
        )

        mock_get_vix_direct.return_value = None
        mock_get_market.return_value = {
            "SPY": {"price": 450.0, "pct": 0.5},
            "QQQ": {"price": 380.0, "pct": 0.3},
        }
        mock_market_trend.return_value = ("BULLISH", "SPY and QQQ above 50MA")
        mock_scan_ticker.return_value = {
            "ticker": "SPY",
            "verdict": "SKIP",
            "reason": "Test",
            "data_quality": "MISSING",
        }

        billy_options_scanner.run()

        mock_wrap_vix.assert_called_once()
        mock_get_vix_direct.assert_not_called()
        self.assertGreater(mock_scan_ticker.call_count, 0)

        vix_values = [
            call_obj[0][1]
            for call_obj in mock_scan_ticker.call_args_list
            if len(call_obj[0]) > 1
        ]
        self.assertIn(22.40, vix_values)
        self.assertGreater(mock_send_telegram.call_count, 0)


if __name__ == "__main__":
    unittest.main()
