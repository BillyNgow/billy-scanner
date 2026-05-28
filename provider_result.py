"""Provider result contract for Billy Scanner.

Milestone 2B-1 only.

This module defines a small, serializable contract for data returned by
external or derived providers. It does not fetch data, place trades, or change
scanner behavior.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Provider(str, Enum):
    ALPHA_VANTAGE = "AlphaVantage"
    YFINANCE = "yfinance"
    BARCHART = "Barchart"
    UNKNOWN = "Unknown"


class DataType(str, Enum):
    PRICE = "price"
    OPTIONS_CHAIN = "options_chain"
    OPTION_LEG = "option_leg"
    IVR = "ivr"
    HV = "hv"
    EARNINGS = "earnings"
    VIX = "vix"
    MARKET_TREND = "market_trend"


class QualityLabel(str, Enum):
    VERIFIED = "VERIFIED"
    ESTIMATED = "ESTIMATED"
    MISSING = "MISSING"
    STALE = "STALE"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ProviderResult:
    provider: Provider
    data_type: DataType
    symbol: str
    ok: bool
    quality: QualityLabel
    value: Any = None
    raw: Any = None
    source_label: str = "UNKNOWN"
    fetched_at_utc: str = ""
    stale: bool = False
    staleness_reason: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["provider"] = self.provider.value
        data["data_type"] = self.data_type.value
        data["quality"] = self.quality.value
        return data


def utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def provider_ok(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    value: Any,
    raw: Any = None,
    source_label: str = "",
    fetched_at_utc: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=True,
        quality=QualityLabel.VERIFIED,
        value=value,
        raw=raw,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        stale=False,
    )


def provider_estimated(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    value: Any,
    raw: Any = None,
    source_label: str = "",
    fetched_at_utc: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=True,
        quality=QualityLabel.ESTIMATED,
        value=value,
        raw=raw,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        stale=False,
    )


def provider_missing(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    error: str = "",
    source_label: str = "",
    fetched_at_utc: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=False,
        quality=QualityLabel.MISSING,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        error=error,
    )


def provider_stale(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    value: Any = None,
    raw: Any = None,
    source_label: str = "",
    fetched_at_utc: str = "",
    staleness_reason: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=False,
        quality=QualityLabel.STALE,
        value=value,
        raw=raw,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        stale=True,
        staleness_reason=staleness_reason,
    )


def provider_rate_limited(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    error: str = "",
    source_label: str = "",
    fetched_at_utc: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=False,
        quality=QualityLabel.RATE_LIMITED,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        error=error,
    )


def provider_error(
    *,
    provider: Provider,
    data_type: DataType,
    symbol: str,
    error: str,
    source_label: str = "",
    fetched_at_utc: str = "",
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        data_type=data_type,
        symbol=symbol,
        ok=False,
        quality=QualityLabel.ERROR,
        source_label=source_label or provider.value,
        fetched_at_utc=fetched_at_utc or utc_now_iso(),
        error=error,
    )


def is_verified(result: ProviderResult) -> bool:
    return result.ok and result.quality == QualityLabel.VERIFIED and not result.stale


def is_take_it_eligible_quality(result: ProviderResult) -> bool:
    """Return whether provider data quality can support TAKE_IT.

    This helper checks only the quality contract. It does not evaluate strategy,
    risk, trend, earnings, liquidity, or broker verification.
    """
    return is_verified(result)
