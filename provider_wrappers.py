"""Provider wrappers for Billy Scanner.

Milestone 2B-2: Non-invasive wrappers around existing provider fetch functions.
Each wrapper calls an existing scanner function and returns a ProviderResult.

Does NOT modify scanner behavior, verdict logic, or strategy rules.
Wrappers are NOT imported by billy_options_scanner.py yet.
Results are secondary artifacts for logging/monitoring only.
"""

from provider_result import (
    ProviderResult,
    Provider,
    DataType,
    provider_ok,
    provider_estimated,
    provider_missing,
    provider_error,
)

# Module-level imports for proper mock patching in tests.
from billy_options_scanner import (
    av_get_price,
    av_get_options_chain,
    get_ivr_barchart,
    get_iv_yfinance,
    get_moving_averages,
    get_vix,
)


def wrap_av_price(ticker: str) -> ProviderResult:
    """Wrap av_get_price() -> ProviderResult[DataType.PRICE]."""
    try:
        result = av_get_price(ticker)

        if result is None:
            return provider_missing(
                provider=Provider.ALPHA_VANTAGE,
                data_type=DataType.PRICE,
                symbol=ticker,
                error="av_get_price returned None",
                source_label="AlphaVantage",
            )

        if result.get("price"):
            return provider_ok(
                provider=Provider.ALPHA_VANTAGE,
                data_type=DataType.PRICE,
                symbol=ticker,
                value=result,
                source_label="AlphaVantage",
            )

        return provider_missing(
            provider=Provider.ALPHA_VANTAGE,
            data_type=DataType.PRICE,
            symbol=ticker,
            error="av_get_price returned empty result",
            source_label="AlphaVantage",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.ALPHA_VANTAGE,
            data_type=DataType.PRICE,
            symbol=ticker,
            error=str(exc),
            source_label="AlphaVantage",
        )


def wrap_av_options_chain(ticker: str) -> ProviderResult:
    """Wrap av_get_options_chain() -> ProviderResult[DataType.OPTIONS_CHAIN]."""
    try:
        result = av_get_options_chain(ticker)

        if not result or (isinstance(result, list) and len(result) == 0):
            return provider_missing(
                provider=Provider.ALPHA_VANTAGE,
                data_type=DataType.OPTIONS_CHAIN,
                symbol=ticker,
                error="av_get_options_chain returned empty or None",
                source_label="AlphaVantage",
            )

        return provider_ok(
            provider=Provider.ALPHA_VANTAGE,
            data_type=DataType.OPTIONS_CHAIN,
            symbol=ticker,
            value=result,
            source_label="AlphaVantage",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.ALPHA_VANTAGE,
            data_type=DataType.OPTIONS_CHAIN,
            symbol=ticker,
            error=str(exc),
            source_label="AlphaVantage",
        )


def wrap_barchart_ivr(ticker: str) -> ProviderResult:
    """Wrap get_ivr_barchart() -> ProviderResult[DataType.IVR]."""
    try:
        result = get_ivr_barchart(ticker)

        if result is None:
            return provider_missing(
                provider=Provider.BARCHART,
                data_type=DataType.IVR,
                symbol=ticker,
                error="Barchart scrape failed or timeout",
                source_label="Barchart",
            )

        if isinstance(result, (int, float)):
            return provider_ok(
                provider=Provider.BARCHART,
                data_type=DataType.IVR,
                symbol=ticker,
                value=result,
                source_label="Barchart",
            )

        return provider_missing(
            provider=Provider.BARCHART,
            data_type=DataType.IVR,
            symbol=ticker,
            error="Barchart returned invalid type",
            source_label="Barchart",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.BARCHART,
            data_type=DataType.IVR,
            symbol=ticker,
            error=str(exc),
            source_label="Barchart",
        )


def wrap_yf_iv_data(ticker: str) -> ProviderResult:
    """Wrap get_iv_yfinance() while preserving partial dictionaries.

    get_iv_yfinance() may return partial data like {"price": X, "hv": Y}
    without an "iv" key. Scanner code historically preserved that partial
    data via yfd.get(...). This wrapper must not discard it.
    """
    try:
        result = get_iv_yfinance(ticker)

        if isinstance(result, dict) and result:
            has_iv = result.get("iv") is not None
            has_partial_fields = any(
                key in result and result.get(key) is not None
                for key in ("price", "hv", "ivr", "samples")
            )

            if has_iv or has_partial_fields:
                return provider_estimated(
                    provider=Provider.YFINANCE,
                    data_type=DataType.IVR,
                    symbol=ticker,
                    value=result,
                    source_label="yfinance",
                )

        return provider_missing(
            provider=Provider.YFINANCE,
            data_type=DataType.IVR,
            symbol=ticker,
            error="get_iv_yfinance returned empty",
            source_label="yfinance",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE,
            data_type=DataType.IVR,
            symbol=ticker,
            error=str(exc),
            source_label="yfinance",
        )


def wrap_moving_averages(ticker: str) -> ProviderResult:
    """Wrap get_moving_averages() -> ProviderResult[DataType.MARKET_TREND]."""
    try:
        result = get_moving_averages(ticker)

        if result is None:
            return provider_missing(
                provider=Provider.YFINANCE,
                data_type=DataType.MARKET_TREND,
                symbol=ticker,
                error="Insufficient price history",
                source_label="yfinance",
            )

        if all(key in result for key in ["price", "ma20", "ma50", "ma200"]):
            return provider_ok(
                provider=Provider.YFINANCE,
                data_type=DataType.MARKET_TREND,
                symbol=ticker,
                value=result,
                source_label="yfinance",
            )

        return provider_missing(
            provider=Provider.YFINANCE,
            data_type=DataType.MARKET_TREND,
            symbol=ticker,
            error="Incomplete moving averages",
            source_label="yfinance",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE,
            data_type=DataType.MARKET_TREND,
            symbol=ticker,
            error=str(exc),
            source_label="yfinance",
        )


def wrap_vix() -> ProviderResult:
    """Wrap get_vix() -> ProviderResult[DataType.VIX]."""
    try:
        result = get_vix()

        if result is None:
            return provider_missing(
                provider=Provider.YFINANCE,
                data_type=DataType.VIX,
                symbol="^VIX",
                error="VIX fetch failed",
                source_label="yfinance",
            )

        if isinstance(result, (int, float)):
            return provider_ok(
                provider=Provider.YFINANCE,
                data_type=DataType.VIX,
                symbol="^VIX",
                value=result,
                source_label="yfinance",
            )

        return provider_missing(
            provider=Provider.YFINANCE,
            data_type=DataType.VIX,
            symbol="^VIX",
            error="VIX returned invalid type",
            source_label="yfinance",
        )

    except Exception as exc:
        return provider_error(
            provider=Provider.YFINANCE,
            data_type=DataType.VIX,
            symbol="^VIX",
            error=str(exc),
            source_label="yfinance",
        )
