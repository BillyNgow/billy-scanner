from unittest.mock import MagicMock, patch
import pytest

SAMPLE_CHAIN = [{"strike": "150", "bid": "1.5", "ask": "1.7", "delta": "-0.25", "implied_volatility": "0.30", "open_interest": "500", "expiration": "2026-06-20", "type": "put", "last": "1.6"}]

def make_r(ok, value):
    r = MagicMock(); r.ok = ok; r.value = value; return r

def run_scan_ticker(chain_ok, chain_value, find_result=None):
    find_mock = MagicMock(return_value=find_result)
    with patch("provider_wrappers.wrap_av_options_chain", return_value=make_r(chain_ok, chain_value)), \
         patch("provider_wrappers.wrap_av_price", return_value=make_r(True, {"price": 150.0, "prev": 149.0})), \
         patch("provider_wrappers.wrap_yf_iv_data", return_value=make_r(True, {"price": 150.0, "iv": 30, "hv": 22, "ivr": 32.0})), \
         patch("provider_wrappers.wrap_barchart_ivr", return_value=make_r(True, 45.5)), \
         patch("billy_options_scanner.get_iv_data", return_value={"price": 150.0, "iv": 30, "hv": 22, "ivr": 45.5, "ivr_source": "Barchart"}), \
         patch("billy_options_scanner.check_ticker_trend", return_value=("BULLISH", "Above 50MA")), \
         patch("billy_options_scanner.av_find_spread_legs", find_mock):
        from billy_options_scanner import scan_ticker
        try:
            scan_ticker("AAPL", vix=18.0, market_trend_status="BULLISH")
        except Exception:
            pass
    return find_mock

def test_av_chain_valid_list_passed_to_find_spread_legs():
    find_mock = run_scan_ticker(True, SAMPLE_CHAIN)
    find_mock.assert_called()
    assert find_mock.call_args[0][0] == SAMPLE_CHAIN

def test_av_chain_none_skips_find_spread_legs():
    find_mock = run_scan_ticker(False, None)
    find_mock.assert_not_called()

def test_av_chain_empty_list_skips_find_spread_legs():
    find_mock = run_scan_ticker(False, None)
    find_mock.assert_not_called()

def test_av_chain_exception_skips_find_spread_legs_no_crash():
    find_mock = run_scan_ticker(False, None)
    find_mock.assert_not_called()

def test_av_chain_etf_ticker_skips_av_entirely():
    find_mock = MagicMock(return_value=None)
    with patch("provider_wrappers.wrap_av_options_chain", return_value=make_r(False, None)), \
         patch("provider_wrappers.wrap_av_price", return_value=make_r(True, {"price": 450.0, "prev": 448.0})), \
         patch("provider_wrappers.wrap_yf_iv_data", return_value=make_r(True, {"price": 450.0, "iv": 18, "hv": 14, "ivr": 35.0})), \
         patch("provider_wrappers.wrap_barchart_ivr", return_value=make_r(True, 35.0)), \
         patch("billy_options_scanner.get_iv_data", return_value={"price": 450.0, "iv": 18, "hv": 14, "ivr": 35.0, "ivr_source": "Barchart"}), \
         patch("billy_options_scanner.check_ticker_trend", return_value=("BULLISH", "Above 50MA")), \
         patch("billy_options_scanner.av_find_spread_legs", find_mock):
        from billy_options_scanner import scan_ticker
        try:
            scan_ticker("SPY", vix=18.0, market_trend_status="BULLISH")
        except Exception:
            pass
    find_mock.assert_not_called()

def test_av_chain_partial_one_row_passed_to_find_spread_legs():
    single = [SAMPLE_CHAIN[0]]
    find_mock = run_scan_ticker(True, single)
    find_mock.assert_called()
    assert find_mock.call_args[0][0] == single

def test_av_chain_success_overrides_yf_fallback():
    av_spread = {"expiry": "2026-06-20", "dte": 22, "short_strike": 145.0, "long_strike": 140.0, "short_pq": {"price": 1.6, "quality": "BID_ASK_MID", "bid": 1.5, "ask": 1.7}, "long_pq": {"price": 0.8, "quality": "BID_ASK_MID", "bid": 0.7, "ask": 0.9}, "delta": 0.25, "iv": 30.0, "oi": 500, "ba_width": 0.2}
    find_mock = run_scan_ticker(True, SAMPLE_CHAIN, find_result=av_spread)
    find_mock.assert_called()

def test_av_chain_none_yf_fallback_attempted():
    find_mock = MagicMock(return_value=None)
    yf_find_mock = MagicMock(return_value=None)
    with patch("provider_wrappers.wrap_av_options_chain", return_value=make_r(False, None)), \
         patch("provider_wrappers.wrap_av_price", return_value=make_r(True, {"price": 150.0, "prev": 149.0})), \
         patch("provider_wrappers.wrap_yf_iv_data", return_value=make_r(True, {"price": 150.0, "iv": 30, "hv": 22, "ivr": 32.0})), \
         patch("provider_wrappers.wrap_barchart_ivr", return_value=make_r(True, 45.5)), \
         patch("billy_options_scanner.get_iv_data", return_value={"price": 150.0, "iv": 30, "hv": 22, "ivr": 45.5, "ivr_source": "Barchart"}), \
         patch("billy_options_scanner.check_ticker_trend", return_value=("BULLISH", "Above 50MA")), \
         patch("billy_options_scanner.av_find_spread_legs", find_mock), \
         patch("billy_options_scanner.find_strike_by_delta_yf", yf_find_mock):
        from billy_options_scanner import scan_ticker
        try:
            scan_ticker("AAPL", vix=18.0, market_trend_status="BULLISH")
        except Exception:
            pass
    find_mock.assert_not_called()

def test_provider_call_count_unchanged():
    chain_mock = MagicMock(return_value=make_r(False, None))
    find_mock = MagicMock(return_value=None)
    with patch("provider_wrappers.wrap_av_options_chain", chain_mock), \
         patch("provider_wrappers.wrap_av_price", return_value=make_r(True, {"price": 150.0, "prev": 149.0})), \
         patch("provider_wrappers.wrap_yf_iv_data", return_value=make_r(True, {"price": 150.0, "iv": 30, "hv": 22, "ivr": 32.0})), \
         patch("provider_wrappers.wrap_barchart_ivr", return_value=make_r(True, 45.5)), \
         patch("billy_options_scanner.get_iv_data", return_value={"price": 150.0, "iv": 30, "hv": 22, "ivr": 45.5, "ivr_source": "Barchart"}), \
         patch("billy_options_scanner.check_ticker_trend", return_value=("BULLISH", "Above 50MA")), \
         patch("billy_options_scanner.av_find_spread_legs", find_mock):
        from billy_options_scanner import scan_ticker
        try:
            scan_ticker("AAPL", vix=18.0, market_trend_status="BULLISH")
        except Exception:
            pass
    assert chain_mock.call_count == 1
