"""
ibkr_provider.py - IBKR MCP data provider for Billy Options Scanner

Replaces AV + yfinance + Barchart for:
  price, IV, HV, IVR (52w percentile), VIX, moving averages

Options chain leg pricing (strike/delta/bid-ask) still uses yfinance/AV
— IBKR MCP does not expose a full options chain endpoint.

Activation: set env var USE_IBKR=true before running the scanner.
In GitHub Actions add  USE_IBKR: "true"  to the workflow env block.

How IBKR MCP calls work in GitHub Actions
------------------------------------------
The scanner calls api.anthropic.com/v1/messages with:
  - mcp_servers: [{"type":"url","url":"https://api.ibkr.com/v1/api/mcp"}]
  - ANTHROPIC_API_KEY from GitHub Secrets

Authentication is inherited from the Claude.ai account that has IBKR
connected. No separate IBKR OAuth or TWS credentials needed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests as _requests

from provider_result import (
    DataType,
    Provider,
    ProviderResult,
    QualityLabel,
    provider_error,
    provider_missing,
    provider_ok,
    provider_estimated,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Contract ID registry — covers the full WATCHLIST
# ---------------------------------------------------------------------------

_KNOWN_CONTRACTS: dict[str, dict] = {
    "SPY":  {"contract_id": 756733,    "exchange": "ARCA"},
    "QQQ":  {"contract_id": 320227571, "exchange": "NASDAQ"},
    "IWM":  {"contract_id": 9579970,   "exchange": "ARCA"},
    "GLD":  {"contract_id": 26718742,  "exchange": "ARCA"},
    "TLT":  {"contract_id": 29542727,  "exchange": "NASDAQ"},
    "XLE":  {"contract_id": 13756721,  "exchange": "ARCA"},
    "XLF":  {"contract_id": 12087792,  "exchange": "ARCA"},
    "AAPL": {"contract_id": 265598,    "exchange": "NASDAQ"},
    "AMD":  {"contract_id": 4391,      "exchange": "NASDAQ"},
    "META": {"contract_id": 107113386, "exchange": "NASDAQ"},
    "AMZN": {"contract_id": 3691937,   "exchange": "NASDAQ"},
    "NVDA": {"contract_id": 4815747,   "exchange": "NASDAQ"},
    "TSLA": {"contract_id": 76792991,  "exchange": "NASDAQ"},
    "PLTR": {"contract_id": 427907534, "exchange": "NYSE"},
    "COIN": {"contract_id": 457651205, "exchange": "NASDAQ"},
    "MSTR": {"contract_id": 8895,      "exchange": "NASDAQ"},
    "^VIX": {"contract_id": 13455763,  "exchange": "CBOE"},
}

_CONTRACT_REGISTRY: dict[str, dict] = {}


def preload_ibkr_contracts(tickers: list[str]) -> dict[str, dict]:
    """Register all known contracts. Call once at scanner startup."""
    _CONTRACT_REGISTRY.update(_KNOWN_CONTRACTS)
    missing = [t for t in tickers if t not in _CONTRACT_REGISTRY]
    if missing:
        print("  [IBKR] No contract_id for: " + str(missing) + " — add to _KNOWN_CONTRACTS")
    return _CONTRACT_REGISTRY


def _get_contract(ticker: str) -> dict | None:
    return _CONTRACT_REGISTRY.get(ticker) or _KNOWN_CONTRACTS.get(ticker)


# ---------------------------------------------------------------------------
# Low-level MCP caller
# ---------------------------------------------------------------------------

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_IBKR_MCP_URL     = "https://api.ibkr.com/v1/api/mcp"


def _call_ibkr_tool(tool_name: str, tool_input: dict, max_tokens: int = 2000) -> dict | None:
    """
    Call any IBKR MCP tool via the Anthropic Messages API.
    Returns the parsed tool result dict, or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  [IBKR] ANTHROPIC_API_KEY not set")
        return None

    # Build a prompt that triggers the specific IBKR tool
    tool_descriptions = {
        "get_price_snapshot": (
            f"Use the IBKR get_price_snapshot tool with these exact parameters: "
            f"contract_id={tool_input['contract_id']}, "
            f"exchange={tool_input['exchange']}, "
            f"market_data_names={tool_input['market_data_names']}. "
            f"Return only the raw tool result, no explanation."
        ),
        "get_price_history": (
            f"Use the IBKR get_price_history tool with these exact parameters: "
            f"contract_id={tool_input['contract_id']}, "
            f"exchange={tool_input['exchange']}, "
            f"security_type={tool_input.get('security_type','STK')}, "
            f"step={tool_input.get('step','ONE_DAY')}, "
            f"period={tool_input.get('period','ONE_YEAR')}, "
            f"outside_rth={str(tool_input.get('outside_rth', False)).lower()}. "
            f"Return only the raw tool result, no explanation."
        ),
    }

    prompt = tool_descriptions.get(tool_name, f"Call IBKR {tool_name} with {tool_input}")

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "mcp_servers": [
            {"type": "url", "url": _IBKR_MCP_URL, "name": "ibkr-mcp"}
        ],
    }

    try:
        resp = _requests.post(
            _ANTHROPIC_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=45,
        )
        if resp.status_code != 200:
            print(f"  [IBKR] API HTTP {resp.status_code}")
            return None
        data = resp.json()
        # Extract mcp_tool_result blocks
        for block in data.get("content", []):
            if block.get("type") == "mcp_tool_result":
                content = block.get("content", [])
                if content and content[0].get("text"):
                    return json.loads(content[0]["text"])
        # Fallback: try parsing text blocks as JSON
        for block in data.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except Exception:
                    pass
        return None
    except Exception as e:
        print(f"  [IBKR] call error: {e}")
        return None


# ---------------------------------------------------------------------------
# Public provider functions
# ---------------------------------------------------------------------------

def ibkr_get_price_and_iv(ticker: str) -> ProviderResult:
    """
    Fetch price + IV + HV + IVR from IBKR.
    Returns ProviderResult with value:
      {"price": float, "iv": float, "hv": float, "ivr": float, "ivr_source": "IBKR"}
    """
    contract = _get_contract(ticker)
    if not contract:
        return provider_missing(
            provider=Provider.UNKNOWN,
            data_type=DataType.IVR,
            symbol=ticker,
            error=f"No IBKR contract_id for {ticker}",
            source_label="IBKR",
        )

    snap = _call_ibkr_tool("get_price_snapshot", {
        "contract_id": contract["contract_id"],
        "exchange": contract["exchange"],
        "market_data_names": [
            "last",
            "prior-close",
            "implied-vol-underlying",
            "historical-vol",
            "implied-volatility-percentile",
        ],
    })

    if not snap:
        return provider_missing(
            provider=Provider.UNKNOWN,
            data_type=DataType.IVR,
            symbol=ticker,
            error="IBKR snapshot returned None",
            source_label="IBKR",
        )

    try:
        last_block  = snap.get("last") or {}
        prior_block = snap.get("prior-close") or {}
        price = last_block.get("price") or prior_block.get("price")
        if price is None:
            return provider_missing(
                provider=Provider.UNKNOWN, data_type=DataType.IVR, symbol=ticker,
                error="No price in IBKR snapshot", source_label="IBKR",
            )
        price = round(float(price), 2)

        iv_block = snap.get("implied-vol-underlying") or {}
        annual_iv_raw = float(iv_block.get("annual_iv", 0) or 0)
        iv = round(annual_iv_raw * 100, 1) if annual_iv_raw < 5 else round(annual_iv_raw, 1)

        hv_block = snap.get("historical-vol") or {}
        annual_hv_raw = float(hv_block.get("annual_pct", 0) or 0)
        hv = round(annual_hv_raw * 100, 1) if annual_hv_raw < 5 else round(annual_hv_raw, 1)

        ivp_block = snap.get("implied-volatility-percentile") or {}
        ivr_raw = ivp_block.get("high_52w")
        if ivr_raw is not None:
            ivr = round(float(ivr_raw) * 100, 1)
        else:
            ivr_26 = ivp_block.get("high_26w")
            ivr = round(float(ivr_26) * 100, 1) if ivr_26 is not None else 0.0

        return provider_ok(
            provider=Provider.UNKNOWN,
            data_type=DataType.IVR,
            symbol=ticker,
            value={"price": price, "iv": iv, "hv": hv, "ivr": ivr, "ivr_source": "IBKR"},
            source_label="IBKR",
            fetched_at_utc=utc_now_iso(),
        )

    except Exception as e:
        return provider_error(
            provider=Provider.UNKNOWN, data_type=DataType.IVR, symbol=ticker,
            error=f"IBKR parse error: {e}", source_label="IBKR",
        )


def ibkr_get_vix() -> ProviderResult:
    """Fetch VIX from IBKR. Returns ProviderResult[DataType.VIX]."""
    contract = _get_contract("^VIX")
    if not contract:
        return provider_missing(
            provider=Provider.UNKNOWN, data_type=DataType.VIX, symbol="^VIX",
            error="No VIX contract registered", source_label="IBKR",
        )

    snap = _call_ibkr_tool("get_price_snapshot", {
        "contract_id": contract["contract_id"],
        "exchange": contract["exchange"],
        "market_data_names": ["last", "prior-close"],
    })

    if not snap:
        return provider_missing(
            provider=Provider.UNKNOWN, data_type=DataType.VIX, symbol="^VIX",
            error="IBKR VIX snapshot None", source_label="IBKR",
        )

    try:
        last_block  = snap.get("last") or {}
        prior_block = snap.get("prior-close") or {}
        price = last_block.get("price") or prior_block.get("price")
        if price is None:
            return provider_missing(
                provider=Provider.UNKNOWN, data_type=DataType.VIX, symbol="^VIX",
                error="No VIX price in snapshot", source_label="IBKR",
            )
        return provider_ok(
            provider=Provider.UNKNOWN, data_type=DataType.VIX, symbol="^VIX",
            value=round(float(price), 2), source_label="IBKR", fetched_at_utc=utc_now_iso(),
        )
    except Exception as e:
        return provider_error(
            provider=Provider.UNKNOWN, data_type=DataType.VIX, symbol="^VIX",
            error=f"VIX parse error: {e}", source_label="IBKR",
        )


def ibkr_get_moving_averages(ticker: str) -> ProviderResult:
    """
    Compute 20/50/200-day MAs from IBKR daily price history.
    Returns ProviderResult[DataType.MARKET_TREND].
    """
    contract = _get_contract(ticker)
    if not contract:
        return provider_missing(
            provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
            error=f"No IBKR contract_id for {ticker}", source_label="IBKR",
        )

    result = _call_ibkr_tool("get_price_history", {
        "contract_id": contract["contract_id"],
        "exchange": contract["exchange"],
        "security_type": "STK",
        "step": "ONE_DAY",
        "period": "ONE_YEAR",
        "outside_rth": False,
    }, max_tokens=4000)

    if not result:
        return provider_missing(
            provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
            error="IBKR price history returned None", source_label="IBKR",
        )

    bars = result.get("bars") or result.get("data") or []
    if len(bars) < 50:
        return provider_missing(
            provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
            error=f"Insufficient IBKR history: {len(bars)} bars", source_label="IBKR",
        )

    try:
        closes = [float(b["close"]) for b in bars if b.get("close") is not None]
        if len(closes) < 50:
            return provider_missing(
                provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
                error="Not enough valid closes", source_label="IBKR",
            )
        price = round(closes[-1], 2)
        ma20  = round(sum(closes[-20:]) / 20, 2)
        ma50  = round(sum(closes[-50:]) / 50, 2)
        ma200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None
        return provider_ok(
            provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
            value={"price": price, "ma20": ma20, "ma50": ma50, "ma200": ma200},
            source_label="IBKR", fetched_at_utc=utc_now_iso(),
        )
    except Exception as e:
        return provider_error(
            provider=Provider.UNKNOWN, data_type=DataType.MARKET_TREND, symbol=ticker,
            error=f"MA compute error: {e}", source_label="IBKR",
        )


def ibkr_get_market_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch price + % change for SPY and QQQ.
    Returns {ticker: {"price": float, "pct": float}}
    """
    out = {}
    for ticker in tickers:
        contract = _get_contract(ticker)
        if not contract:
            continue
        snap = _call_ibkr_tool("get_price_snapshot", {
            "contract_id": contract["contract_id"],
            "exchange": contract["exchange"],
            "market_data_names": ["last", "prior-close", "change"],
        })
        if not snap:
            continue
        try:
            last_block   = snap.get("last") or {}
            change_block = snap.get("change") or {}
            price = last_block.get("price")
            pct   = change_block.get("pct_change") or change_block.get("percent") or 0
            if price is not None:
                out[ticker] = {"price": round(float(price), 2), "pct": round(float(pct), 2)}
        except Exception:
            continue
    return out
