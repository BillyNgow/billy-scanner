"""Unit tests for _av_price_quality() and av_find_spread_legs().

These are the two core spread-finding functions with zero prior coverage.
All tests use dynamically-computed dates so they work on any run date.
"""

from __future__ import annotations

import datetime

import billy_options_scanner as scanner

# ── helpers ──────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _put(expiry_days: int, strike: float, delta: float,
         bid: float, ask: float, last: float,
         iv: float = 0.25, oi: int = 150) -> dict:
    return {
        "type": "put",
        "expiration": _expiry(expiry_days),
        "strike": str(strike),
        "delta": str(-abs(delta)),
        "bid": str(bid),
        "ask": str(ask),
        "last": str(last),
        "implied_volatility": str(iv),
        "open_interest": str(oi),
    }


# ── _av_price_quality ─────────────────────────────────────────────────────────

def test_price_quality_bid_ask_mid():
    pq = scanner._av_price_quality("1.00", "1.50", "1.20")
    assert pq["quality"] == "BID_ASK_MID"
    assert pq["price"] == 1.25
    assert pq["bid"] == 1.00
    assert pq["ask"] == 1.50


def test_price_quality_last_price_only_when_no_valid_spread():
    # bid=0, ask=0, but last > 0
    pq = scanner._av_price_quality("0", "0", "1.30")
    assert pq["quality"] == "LAST_PRICE_ONLY"
    assert pq["price"] == 1.30


def test_price_quality_last_price_only_when_ask_not_gt_bid():
    # inverted bid/ask
    pq = scanner._av_price_quality("1.50", "1.00", "1.20")
    assert pq["quality"] == "LAST_PRICE_ONLY"
    assert pq["price"] == 1.20


def test_price_quality_missing_when_nothing_usable():
    pq = scanner._av_price_quality("0", "0", "0")
    assert pq["quality"] == "MISSING"
    assert pq["price"] is None
    assert pq["bid"] is None
    assert pq["ask"] is None


def test_price_quality_mid_is_average_of_bid_and_ask():
    pq = scanner._av_price_quality("2.00", "3.00", "0")
    assert pq["quality"] == "BID_ASK_MID"
    assert pq["price"] == 2.50


# ── av_find_spread_legs — empty / no valid puts ───────────────────────────────

def test_returns_none_for_empty_list():
    assert scanner.av_find_spread_legs([], 0.30) is None


def test_returns_none_when_all_puts_outside_dte_window():
    chain = [
        _put(10, 430.0, 0.30, 1.0, 1.5, 1.2),   # DTE < MIN_DTE (25)
        _put(60, 425.0, 0.30, 0.8, 1.2, 1.0),   # DTE > MAX_DTE (52)
    ]
    assert scanner.av_find_spread_legs(chain, 0.30) is None


def test_returns_none_when_all_puts_have_zero_iv():
    chain = [_put(45, 430.0, 0.30, 1.0, 1.5, 1.2, iv=0.001)]
    assert scanner.av_find_spread_legs(chain, 0.30) is None


def test_returns_none_when_only_calls_in_window():
    chain = [
        {"type": "call", "expiration": _expiry(45), "strike": "430",
         "delta": "0.30", "bid": "1.0", "ask": "1.5", "last": "1.2",
         "implied_volatility": "0.25", "open_interest": "100"},
    ]
    assert scanner.av_find_spread_legs(chain, 0.30) is None


# ── av_find_spread_legs — expiry selection ────────────────────────────────────

def test_selects_expiry_closest_to_target_dte():
    # DTE=30 is 15 away from TARGET_DTE=45; DTE=44 is 1 away — should pick DTE=44
    chain = [
        _put(30, 440.0, 0.30, 1.0, 1.5, 1.2),
        _put(30, 435.0, 0.28, 0.8, 1.2, 1.0),
        _put(44, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(44, 425.0, 0.28, 1.5, 2.0, 1.8),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    expected_exp = _expiry(44)
    assert result["expiry"] == expected_exp


def test_dte_stored_in_result():
    chain = [
        _put(40, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(40, 425.0, 0.28, 1.5, 2.0, 1.8),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    assert result["dte"] == 40


# ── av_find_spread_legs — strike selection ────────────────────────────────────

def test_short_strike_selected_by_delta_proximity():
    # target_delta=0.30; put with delta=-0.29 should win over delta=-0.20
    chain = [
        _put(45, 440.0, 0.20, 1.0, 1.5, 1.2),
        _put(45, 430.0, 0.29, 2.0, 2.5, 2.2),
        _put(45, 425.0, 0.28, 1.8, 2.2, 2.0),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    assert result["short_strike"] == 430.0


def test_long_strike_is_short_minus_spread_width():
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(45, 425.0, 0.28, 1.5, 2.0, 1.8),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    assert result["long_strike"] == result["short_strike"] - scanner.SPREAD_WIDTH


def test_long_pq_missing_when_no_matching_strike_in_chain():
    # Only the short strike is in the chain, long strike (short - 5) is absent
    chain = [_put(45, 430.0, 0.30, 2.0, 2.5, 2.2)]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    assert result["long_pq"]["quality"] == "MISSING"
    assert result["long_pq"]["price"] is None


def test_long_pq_populated_when_matching_strike_present():
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(45, 425.0, 0.20, 1.0, 1.5, 1.2),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result is not None
    assert result["long_pq"]["quality"] == "BID_ASK_MID"
    assert result["long_pq"]["price"] == 1.25


# ── av_find_spread_legs — result shape and IV scaling ────────────────────────

def test_result_has_all_expected_keys():
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(45, 425.0, 0.20, 1.0, 1.5, 1.2),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    for key in ("expiry", "dte", "short_strike", "long_strike",
                "short_pq", "long_pq", "delta", "iv", "oi",
                "ba_width", "delta_method", "source"):
        assert key in result, f"Missing key: {key}"


def test_iv_scaling_decimal_to_percent():
    # implied_volatility=0.25 → iv=25.0 (< 3, so * 100)
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2, iv=0.25),
        _put(45, 425.0, 0.20, 1.0, 1.5, 1.2, iv=0.20),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result["iv"] == 25.0


def test_iv_already_percent_not_double_scaled():
    # implied_volatility=25.0 → iv=25.0 (>= 3, used as-is)
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2, iv=25.0),
        _put(45, 425.0, 0.20, 1.0, 1.5, 1.2, iv=20.0),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result["iv"] == 25.0


def test_source_label_is_alphavantage():
    chain = [
        _put(45, 430.0, 0.30, 2.0, 2.5, 2.2),
        _put(45, 425.0, 0.20, 1.0, 1.5, 1.2),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result["source"] == "AlphaVantage"
    assert result["delta_method"] == "AV Greeks"


def test_ba_width_computed_from_short_pq():
    # short put: bid=2.00, ask=2.50 → ba_width=0.50
    chain = [
        _put(45, 430.0, 0.30, 2.00, 2.50, 2.20),
        _put(45, 425.0, 0.20, 1.00, 1.50, 1.20),
    ]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result["ba_width"] == 0.50


def test_ba_width_is_999_when_no_valid_spread():
    # missing bid/ask on short
    chain = [_put(45, 430.0, 0.30, 0.0, 0.0, 2.20)]
    result = scanner.av_find_spread_legs(chain, 0.30)
    assert result["ba_width"] == 999
