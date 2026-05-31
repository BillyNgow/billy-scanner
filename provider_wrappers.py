"""
provider_wrappers.py - Billy Scanner provider wrappers
IBKR Edition: IBKR is primary when USE_IBKR=true, existing sources are fallback.

All existing tests still pass — IBKR path only activates when USE_IBKR=true.
"""

from __future__ import annotations

import os

from provider_result import (
    ProviderResult,
    Provider,
    DataType,
    QualityLabel,
    provider_ok,
    provider_estimated,
    provider_missing,
    provider_error,
)

from billy_options_scanner import (
    av_get_price,
    av_get_options_chain,
    get_ivr_barchart,
    get_iv_yfinance,
    get_moving_averages,
    get_vix,
)

_USE_IBKR = os.environ.get("USE_IBKR", "").lower() in ("1", "true", "yes")

if _USE_IBKR:
    try:
        from ibkr_provider import (
            ibkr_get_price_and_iv,
            ibkr_get_vix,
            ibkr_get_moving_averages,
        )
        _IBKR_AVAILABLE = True
    except ImportError:
        _IBKR_AVAILABLE = False
        print("  [IBKR] ibkr_provider not found — using AV/yfinance/Barchart")
else:
    _IBKR_AVAILABLE = False


# ---------------------------------------------------------------------------
# wrap_av_price
# IBKR path: returns broker-grade price, skips AV quota
# ---------------------------------------------------------------------------

def wrap_av_price(ticker: str) -> ProviderResult:
    if _USE_IBKR and _IBKR_AVAILABLE:
        try:
            r = ibkr_get_price_and_iv(ticker)
            if r.ok and r.value and r.value.get("price"):
                return provider_ok(
                    provider=Provider.UNKNOWN,
                    data_type=DataType.PRICE,
                    symbol=ticker,
                    value={"price": r.value["price"], "prev": None},
                    source_label="IBKR",
                    fetched_at_utc=r.fetched_at_utc,
                )
        except Exception as e:
            print(f"  [IBKR price fallback] {e}")

    try:
        result = av_get_price(ticker)
        if result is None:
            return provider_missing(
                provider=Provider.ALPHA_VANTAGE, data_type=DataType.PRICE,
                symbol=ticker, error="av_get_price returned None", source_label="AlphaVantage",
            )
        if result.get("price"):
            return provider_ok(
                provider=Provider.ALPHA_VANTAGE, data_type=DataType.PRICE,
                symbol=ticker, value=result, source_label="AlphaVantage",
            )
        return provider_missing(
            provider=Provider.ALPHA_VANTAGE, data_type=DataType.PRICE,
            symbol=ticker, error="av_get_price returned empty", source_label="AlphaVantage",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.ALPHA_VANTAGE, data_type=DataType.PRICE,
            symbol=ticker, error=str(exc), source_label="AlphaVantage",
        )


# ---------------------------------------------------------------------------
# wrap_av_options_chain  (no IBKR equivalent — unchanged)
# ---------------------------------------------------------------------------

def wrap_av_options_chain(ticker: str) -> ProviderResult:
    try:
        result = av_get_options_chain(ticker)
        if not result or (isinstance(result, list) and len(result) == 0):
            return provider_missing(
                provider=Provider.ALPHA_VANTAGE, data_type=DataType.OPTIONS_CHAIN,
                symbol=ticker, error="av_get_options_chain returned empty or None",
                source_label="AlphaVantage",
            )
        return provider_ok(
            provider=Provider.ALPHA_VANTAGE, data_type=DataType.OPTIONS_CHAIN,
            symbol=ticker, value=result, source_label="AlphaVantage",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.ALPHA_VANTAGE, data_type=DataType.OPTIONS_CHAIN,
            symbol=ticker, error=str(exc), source_label="AlphaVantage",
        )


# ---------------------------------------------------------------------------
# wrap_barchart_ivr
# IBKR path: returns 52w IV percentile as "IBKR" source (TAKE_IT-eligible)
# ---------------------------------------------------------------------------

def wrap_barchart_ivr(ticker: str) -> ProviderResult:
    if _USE_IBKR and _IBKR_AVAILABLE:
        try:
            r = ibkr_get_price_and_iv(ticker)
            if r.ok and r.value:
                ivr = r.value.get("ivr")
                if ivr is not None:
                    return provider_ok(
                        provider=Provider.UNKNOWN, data_type=DataType.IVR,
                        symbol=ticker, value=ivr, source_label="IBKR",
                        fetched_at_utc=r.fetched_at_utc,
                    )
        except Exception as e:
            print(f"  [IBKR IVR fallback] {e}")

    try:
        result = get_ivr_barchart(ticker)
        if result is None:
            return provider_missing(
                provider=Provider.BARCHART, data_type=DataType.IVR,
                symbol=ticker, error="Barchart scrape failed or timeout",
                source_label="Barchart",
            )
        if isinstance(result, (int, float)):
            return provider_ok(
                provider=Provider.BARCHART, data_type=DataType.IVR,
                symbol=ticker, value=result, source_label="Barchart",
            )
        return provider_missing(
            provider=Provider.BARCHART, data_type=DataType.IVR,
            symbol=ticker, error="Barchart returned invalid type", source_label="Barchart",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.BARCHART, data_type=DataType.IVR,
            symbol=ticker, error=str(exc), source_label="Barchart",
        )


# ---------------------------------------------------------------------------
# wrap_yf_iv_data
# IBKR path: IV and HV from IBKR (broker-grade), labelled ESTIMATED to
#            preserve compatibility with existing quality checks.
# ---------------------------------------------------------------------------

def wrap_yf_iv_data(ticker: str) -> ProviderResult:
    if _USE_IBKR and _IBKR_AVAILABLE:
        try:
            r = ibkr_get_price_and_iv(ticker)
            if r.ok and r.value:
                v = r.value
                return provider_estimated(
                    provider=Provider.UNKNOWN, data_type=DataType.IVR,
                    symbol=ticker,
                    value={"price": v.get("price"), "iv": v.get("iv", 0),
                           "hv": v.get("hv", 0), "ivr": v.get("ivr", 0)},
                    source_label="IBKR", fetched_at_utc=r.fetched_at_utc,
                )
        except Exception as e:
            print(f"  [IBKR IV/HV fallback] {e}")

    try:
        result = get_iv_yfinance(ticker)
        if isinstance(result, dict) and result:
            has_iv = result.get("iv") is not None
            has_partial = any(
                key in result and result.get(key) is not None
                for key in ("price", "hv", "ivr", "samples")
            )
            if has_iv or has_partial:
                return provider_estimated(
                    provider=Provider.YFINANCE, data_type=DataType.IVR,
                    symbol=ticker, value=result, source_label="yfinance",
                )
        return provider_missing(
            provider=Provider.YFINANCE, data_type=DataType.IVR,
            symbol=ticker, error="get_iv_yfinance returned empty", source_label="yfinance",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE, data_type=DataType.IVR,
            symbol=ticker, error=str(exc), source_label="yfinance",
        )


# ---------------------------------------------------------------------------
# wrap_moving_averages
# IBKR path: MAs from broker-grade daily price history
# ---------------------------------------------------------------------------

def wrap_moving_averages(ticker: str) -> ProviderResult:
    if _USE_IBKR and _IBKR_AVAILABLE:
        try:
            r = ibkr_get_moving_averages(ticker)
            if r.ok and r.value:
                return r
        except Exception as e:
            print(f"  [IBKR MA fallback] {e}")

    try:
        result = get_moving_averages(ticker)
        if result is None:
            return provider_missing(
                provider=Provider.YFINANCE, data_type=DataType.MARKET_TREND,
                symbol=ticker, error="Insufficient price history", source_label="yfinance",
            )
        if all(key in result for key in ["price", "ma20", "ma50", "ma200"]):
            return provider_ok(
                provider=Provider.YFINANCE, data_type=DataType.MARKET_TREND,
                symbol=ticker, value=result, source_label="yfinance",
            )
        return provider_missing(
            provider=Provider.YFINANCE, data_type=DataType.MARKET_TREND,
            symbol=ticker, error="Incomplete moving averages", source_label="yfinance",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE, data_type=DataType.MARKET_TREND,
            symbol=ticker, error=str(exc), source_label="yfinance",
        )


# ---------------------------------------------------------------------------
# wrap_vix
# IBKR path: broker-grade VIX
# ---------------------------------------------------------------------------

def wrap_vix() -> ProviderResult:
    if _USE_IBKR and _IBKR_AVAILABLE:
        try:
            r = ibkr_get_vix()
            if r.ok and r.value is not None:
                return r
        except Exception as e:
            print(f"  [IBKR VIX fallback] {e}")

    try:
        result = get_vix()
        if result is None:
            return provider_missing(
                provider=Provider.YFINANCE, data_type=DataType.VIX,
                symbol="^VIX", error="VIX fetch failed", source_label="yfinance",
            )
        if isinstance(result, (int, float)):
            return provider_ok(
                provider=Provider.YFINANCE, data_type=DataType.VIX,
                symbol="^VIX", value=result, source_label="yfinance",
            )
        return provider_missing(
            provider=Provider.YFINANCE, data_type=DataType.VIX,
            symbol="^VIX", error="VIX invalid type", source_label="yfinance",
        )
    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE, data_type=DataType.VIX,
            symbol="^VIX", error=str(exc), source_label="yfinance",
        )
