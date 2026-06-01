"""Unit tests for vix_label(), vix_size_modifier(), size_contracts(),
calc_metrics(), and ticker_category().

These pure-function utilities had zero test coverage despite directly
controlling trade placement and position sizing.
"""

from __future__ import annotations

import billy_options_scanner as scanner

# ── vix_label ─────────────────────────────────────────────────────────────────

def test_vix_label_none_is_unknown():
    assert scanner.vix_label(None) == "Unknown"

def test_vix_label_below_15_is_low_fear():
    assert scanner.vix_label(14.9) == "Low Fear"
    assert scanner.vix_label(10.0) == "Low Fear"

def test_vix_label_15_to_20_is_neutral():
    assert scanner.vix_label(15.0) == "Neutral"
    assert scanner.vix_label(19.9) == "Neutral"

def test_vix_label_20_to_25_is_slightly_elevated():
    label = scanner.vix_label(20.0)
    assert "Slightly Elevated" in label

def test_vix_label_25_to_30_is_elevated():
    label = scanner.vix_label(25.0)
    assert "Elevated" in label
    assert "reduce" in label.lower()

def test_vix_label_30_plus_is_high_fear():
    label = scanner.vix_label(30.0)
    assert "HIGH FEAR" in label
    assert scanner.vix_label(45.0) == scanner.vix_label(30.0)


# ── vix_size_modifier ─────────────────────────────────────────────────────────

def test_vix_size_modifier_none_returns_full():
    assert scanner.vix_size_modifier(None) == 1.0

def test_vix_size_modifier_below_25_returns_full():
    assert scanner.vix_size_modifier(14.0) == 1.0
    assert scanner.vix_size_modifier(24.9) == 1.0

def test_vix_size_modifier_25_to_30_returns_half():
    assert scanner.vix_size_modifier(25.0) == 0.5
    assert scanner.vix_size_modifier(29.9) == 0.5

def test_vix_size_modifier_30_returns_zero():
    assert scanner.vix_size_modifier(30.0) == 0.0

def test_vix_size_modifier_above_30_returns_zero():
    assert scanner.vix_size_modifier(40.0) == 0.0
    assert scanner.vix_size_modifier(55.0) == 0.0

def test_vix_size_modifier_boundary_exactly_25():
    # 25 falls in the >= 25 and < 30 bucket
    assert scanner.vix_size_modifier(25.0) == 0.5

def test_vix_size_modifier_boundary_exactly_30():
    assert scanner.vix_size_modifier(30.0) == 0.0


# ── size_contracts ────────────────────────────────────────────────────────────

def test_size_contracts_zero_max_loss_returns_minimum():
    # Guard: max_loss_per_contract <= 0 always returns 1
    assert scanner.size_contracts(0) == 1
    assert scanner.size_contracts(-10) == 1

def test_size_contracts_basic_one_contract():
    # MAX_RISK_USD=500; max_loss=400 → floor(500/400)=1
    assert scanner.size_contracts(400, 1.0) == 1

def test_size_contracts_two_contracts():
    # max_loss=200 → floor(500/200)=2
    assert scanner.size_contracts(200, 1.0) == 2

def test_size_contracts_floored_not_rounded():
    # max_loss=300 → floor(500/300)=1 (not 2)
    assert scanner.size_contracts(300, 1.0) == 1

def test_size_contracts_size_mod_halves_capacity():
    # max_loss=200, size_mod=0.5 → floor(250/200)=1
    assert scanner.size_contracts(200, 0.5) == 1

def test_size_contracts_minimum_is_one_even_with_large_loss():
    # max_loss=1000 > MAX_RISK_USD=500; floor(500/1000)=0 → clamp to 1
    assert scanner.size_contracts(1000, 1.0) == 1

def test_size_contracts_size_mod_zero_returns_minimum():
    # size_mod=0 → floor(0/200)=0 → clamp to 1
    assert scanner.size_contracts(200, 0.0) == 1


# ── calc_metrics ──────────────────────────────────────────────────────────────

def test_calc_metrics_returns_all_fields():
    m = scanner.calc_metrics(2.00, 5, 1)
    for key in ("np_usd", "nl_usd", "np_rm", "nl_rm", "fees", "credit_width_proxy"):
        assert key in m, f"Missing key: {key}"

def test_calc_metrics_net_profit_single_contract():
    # gross_profit=200, fees=3.16, np_usd=196.84
    m = scanner.calc_metrics(2.00, 5, 1)
    assert m["np_usd"] == 196.84

def test_calc_metrics_net_loss_single_contract():
    # gross_loss=300, fees=3.16, nl_usd=303.16
    m = scanner.calc_metrics(2.00, 5, 1)
    assert m["nl_usd"] == 303.16

def test_calc_metrics_fees_two_legs_per_contract():
    # fees = IBKR_FEE * 2 legs * 2 sides (open+close) * contracts
    # = 0.79 * 2 * 2 * 1 = 3.16
    m = scanner.calc_metrics(2.00, 5, 1)
    assert m["fees"] == 3.16

def test_calc_metrics_fees_scale_with_contracts():
    m1 = scanner.calc_metrics(2.00, 5, 1)
    m2 = scanner.calc_metrics(2.00, 5, 2)
    assert m2["fees"] == round(m1["fees"] * 2, 2)

def test_calc_metrics_myr_conversion():
    m = scanner.calc_metrics(2.00, 5, 1)
    assert m["np_rm"] == round(m["np_usd"] * scanner.USD_MYR_RATE, 2)
    assert m["nl_rm"] == round(m["nl_usd"] * scanner.USD_MYR_RATE, 2)

def test_calc_metrics_multi_contract_scales_pnl():
    m1 = scanner.calc_metrics(2.00, 5, 1)
    m2 = scanner.calc_metrics(2.00, 5, 2)
    # gross_profit and gross_loss scale linearly with contracts
    assert m2["np_usd"] > m1["np_usd"]
    assert m2["nl_usd"] > m1["nl_usd"]

def test_calc_metrics_credit_width_proxy_ratio():
    m = scanner.calc_metrics(1.50, 5, 1)
    assert m["credit_width_proxy"] == 0.30  # 1.50 / 5.00

def test_calc_metrics_proxy_clamped_above_one():
    m = scanner.calc_metrics(10.0, 5, 1)
    assert m["credit_width_proxy"] == 1.0

def test_calc_metrics_proxy_clamped_below_zero():
    m = scanner.calc_metrics(1.0, 0, 1)  # width=0
    assert m["credit_width_proxy"] == 0.0

def test_calc_metrics_credit_equals_width_gives_proxy_one():
    m = scanner.calc_metrics(5.0, 5, 1)
    assert m["credit_width_proxy"] == 1.0


# ── ticker_category ───────────────────────────────────────────────────────────

def test_ticker_category_etf():
    for ticker in ["SPY", "QQQ", "IWM", "GLD", "TLT"]:
        assert scanner.ticker_category(ticker) == "ETF"

def test_ticker_category_high_risk():
    for ticker in ["TSLA", "NVDA", "COIN", "MSTR", "PLTR"]:
        assert scanner.ticker_category(ticker) == "HIGH_RISK"

def test_ticker_category_normal():
    for ticker in ["AAPL", "AMD", "META", "AMZN"]:
        assert scanner.ticker_category(ticker) == "NORMAL"

def test_ticker_category_unknown_defaults_to_normal():
    assert scanner.ticker_category("UNKNOWN_TICKER_XYZ") == "NORMAL"
