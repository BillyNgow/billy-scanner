#!/usr/bin/env python3
"""
Billy Options Scanner - Cloud Version (GitHub Actions)
Framework: Tom Sosnoff / tastytrade bull put spread

Data sources (priority order):
  1. Alpha Vantage - price (GLOBAL_QUOTE) + options (HISTORICAL_OPTIONS)
  2. yfinance      - HV, IVR calc, options chain fallback, VIX, earnings
  3. Barchart      - IVR scrape (most reliable IVR source)

No IBKR / TWS required. Runs headless on GitHub Actions.
Schedule (cron in scan.yml): 30 20 * * 1-5
  = 20:30 UTC weekdays
  = 04:30 AM MYT (next day) during US daylight saving time
  = ~30 min after US market close (16:00 ET)
  Note: fixed UTC cron does not perfectly handle US DST shifts.

Secrets (set in GitHub repo -> Settings -> Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, AV_API_KEY

IMPORTANT: This scanner is for EDUCATIONAL and PERSONAL SCREENING only.
It does NOT place trades. All signals must be verified manually in your
broker before acting. Nothing here is financial advice.

Verdicts:
  TAKE_IT      = all required live/verified data passed every check
  MANUAL_CHECK = possible setup but data quality issues found
  SKIP         = failed a hard rule
"""

import os, re, sys, json, csv, datetime, time, math, warnings, argparse
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd
import billy_health

# --- CONFIG -----------------------------------------------------------
# Credentials - from GitHub Secrets (never hardcode)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
AV_API_KEY       = os.environ.get("AV_API_KEY", "")

# Account
ACCOUNT_SIZE_USD = 25000
MAX_RISK_PCT     = 0.02
MAX_RISK_USD     = ACCOUNT_SIZE_USD * MAX_RISK_PCT  # $500 USD
USD_MYR_RATE     = 4.40

# Portfolio limits (per-scan, since scanner does not know open positions)
# Note: MAX_TOTAL_OPEN_RISK_PCT removed - scanner has no view of existing positions.
# See README.md for limitations.
MAX_TRADES_PER_SCAN        = 2
MAX_HIGH_RISK_STOCK_TRADES = 1

# Ticker classification
ETF_LIST = [
    "SPY","QQQ","IWM","DIA","GLD","TLT","USO","SLV",
    "EEM","XLE","XLF","FXI","ARKK","SOXX"
]
HIGH_RISK_STOCKS = ["TSLA","NVDA","COIN","MSTR","PLTR"]
NORMAL_STOCKS    = ["AAPL","AMD","META","AMZN"]

# Watchlist (ETFs first - preferred)
WATCHLIST = [
    "SPY","QQQ","IWM","GLD","TLT","XLE","XLF",
    "AAPL","AMD","META","AMZN",
    "NVDA","TSLA","PLTR",
    "COIN","MSTR"
]

# Entry rules
MIN_IV_RANK      = 30
MIN_DTE          = 25
MAX_DTE          = 52
TARGET_DTE       = 45
SPREAD_WIDTH     = 5
MIN_CREDIT_RATIO = 0.33
EARNINGS_BUFFER  = 14
IBKR_FEE         = 0.79

# Delta
TARGET_DELTA_LOW  = 0.20
TARGET_DELTA_HIGH = 0.35
TARGET_DELTA      = 0.30

# Liquidity
MIN_OPEN_INTEREST    = 50
MAX_BID_ASK_WIDTH    = 0.50
HR_MIN_IV_RANK       = 50
HR_MAX_BID_ASK_WIDTH = 0.30

# Alpha Vantage
AV_BASE             = "https://www.alphavantage.co/query"
# AV_PRE_PROBE_CALLS counts Alpha Vantage calls made BEFORE scan_ticker
# starts, for example the validate-config health probe. Quota total is:
# AV total = AV_PRE_PROBE_CALLS + AV_CALL_COUNT.
AV_PRE_PROBE_CALLS  = 0
AV_CALL_COUNT       = 0
AV_FREE_LIMIT       = 25

# Output / journal
OUTPUT_DIR = "output"

# --- TELEGRAM ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [Telegram not configured]")
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print("  Telegram OK" if r.status_code == 200 else "  Telegram err:" + str(r.status_code))
    except Exception as e:
        print("  Telegram error: " + str(e))


# --- FEE & METRIC HELPERS ---------------------------------------------
def calc_fees(contracts=1):
    return round(IBKR_FEE * 2 * 2 * contracts, 2)

def calc_metrics(credit, width, contracts=1):
    gross_profit = credit * 100 * contracts
    gross_loss   = (width - credit) * 100 * contracts
    fees = calc_fees(contracts)
# credit_width_proxy = credit / width. NOT a probability of profit.
# Replaces the previous "pop" label, which was a placeholder, not a
# broker-grade probability. Clamped to [0.0, 1.0].
cw = round(max(0.0, min(1.0, credit / width)), 4) if width > 0 else 0.0
    return {
        "np_usd": round(gross_profit - fees, 2),
        "nl_usd": round(gross_loss + fees, 2),
        "np_rm" : round((gross_profit - fees) * USD_MYR_RATE, 2),
        "nl_rm" : round((gross_loss + fees) * USD_MYR_RATE, 2),
        "fees"  : round(fees, 2),
        "credit_width_proxy": cw,
    }

def size_contracts(max_loss_per_contract, size_mod=1.0):
    if max_loss_per_contract <= 0:
        return 1
    return max(1, math.floor((MAX_RISK_USD * size_mod) / max_loss_per_contract))

def ticker_category(ticker):
    if ticker in ETF_LIST:
        return "ETF"
    if ticker in HIGH_RISK_STOCKS:
        return "HIGH_RISK"
    return "NORMAL"


# --- VIX HELPERS ------------------------------------------------------
def get_vix():
    try:
        h = yf.Ticker("^VIX").history(period="5d")
        return round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
    except:
        return None

def vix_label(v):
    if v is None: return "Unknown"
    if v < 15:    return "Low Fear"
    if v < 20:    return "Neutral"
    if v < 25:    return "Slightly Elevated - trade smaller"
    if v < 30:    return "Elevated - reduce size"
    return "HIGH FEAR - stand aside"

def vix_size_modifier(v):
    if v is None: return 1.0
    if v >= 30:   return 0.0
    if v >= 25:   return 0.5
    return 1.0

# --- TREND FILTER -----------------------------------------------------
def get_moving_averages(ticker):
    """Return {price, ma20, ma50, ma200} or None on failure."""
    try:
        h = yf.Ticker(ticker).history(period="220d")
        if len(h) < 50:
            return None
        price = float(h["Close"].iloc[-1])
        ma20  = float(h["Close"].tail(20).mean())
        ma50  = float(h["Close"].tail(50).mean())
        ma200 = float(h["Close"].tail(200).mean()) if len(h) >= 200 else None
        return {
            "price": round(price, 2),
            "ma20" : round(ma20, 2),
            "ma50" : round(ma50, 2),
            "ma200": round(ma200, 2) if ma200 else None,
        }
    except:
        return None

def check_market_trend():
    """
    Returns (status, reason).
    status: "BULLISH" | "BEARISH" | "UNKNOWN"
    SAFETY RULE: UNKNOWN must block TAKE_IT (downgrade to MANUAL_CHECK).
    """
    spy = get_moving_averages("SPY")
    qqq = get_moving_averages("QQQ")
    if spy is None or qqq is None:
        return "UNKNOWN", "Market trend data unavailable - no TAKE_IT allowed"
    spy_above = spy["price"] >= spy["ma50"]
    qqq_above = qqq["price"] >= qqq["ma50"]
    detail = (
        "SPY $" + str(spy["price"]) + " vs 50MA $" + str(spy["ma50"]) + " | "
        + "QQQ $" + str(qqq["price"]) + " vs 50MA $" + str(qqq["ma50"])
    )
    if not spy_above and not qqq_above:
        return "BEARISH", "SPY and QQQ both below 50MA - " + detail
    return "BULLISH", detail

def check_ticker_trend(ticker, price):
    """
    Returns (status, detail).
    status: "BULLISH" | "CAUTION" | "BEARISH" | "UNKNOWN"
    SAFETY RULE: Only BULLISH allows TAKE_IT.
    Anything else must downgrade to MANUAL_CHECK or SKIP.
    """
    ma = get_moving_averages(ticker)
    if ma is None:
        return "UNKNOWN", "Could not fetch moving averages"
    above_50  = price >= ma["ma50"]
    above_200 = (price >= ma["ma200"]) if ma["ma200"] else None
    detail = (
        "Price $" + str(price) + " | 50MA $" + str(ma["ma50"])
        + " | 200MA $" + str(ma["ma200"] or "N/A")
    )
    if above_200 is False:
        return "BEARISH", "Below 200MA - " + detail
    if not above_50:
        return "CAUTION", "Below 50MA - " + detail
    return "BULLISH", detail


# --- EARNINGS ---------------------------------------------------------
def check_earnings(ticker):
    """
    Returns (safe, days, date_str, status).
    status: "ETF" | "CONFIRMED" | "UNKNOWN"
    UNKNOWN for a stock must downgrade to MANUAL_CHECK.
    """
    if ticker in ETF_LIST:
        return True, 999, "ETF - no earnings", "ETF"
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date", [])
            if ed:
                dt   = pd.Timestamp(ed[0]).date()
                days = (dt - datetime.date.today()).days
                return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y"), "CONFIRMED"
    except:
        pass
    return True, 999, "Unknown", "UNKNOWN"

# --- ALPHA VANTAGE HELPERS --------------------------------------------
def _av_get(params):
    """Single AV API call with quota guard."""
    global AV_CALL_COUNT
    if (AV_PRE_PROBE_CALLS + AV_CALL_COUNT) >= AV_FREE_LIMIT:
        print("  [AV quota " + str(AV_PRE_PROBE_CALLS + AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT) + " reached]")
        return None
    if not AV_API_KEY:
        return None
    try:
        params["apikey"] = AV_API_KEY
        r = requests.get(AV_BASE, params=params, timeout=15)
        AV_CALL_COUNT += 1
        if r.status_code != 200:
            print("  AV HTTP " + str(r.status_code))
            return None
        data = r.json()
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information", "")
            print("  AV rate-limit: " + str(msg)[:80])
            AV_CALL_COUNT = AV_FREE_LIMIT
            return None
        return data
    except Exception as e:
        print("  AV error: " + str(e))
        return None

def av_get_price(ticker):
    """AV GLOBAL_QUOTE -> price + previous close."""
    data = _av_get({"function": "GLOBAL_QUOTE", "symbol": ticker})
    if not data:
        return None
    q = data.get("Global Quote", {})
    price = q.get("05. price")
    prev  = q.get("08. previous close")
    if not price:
        return None
    return {
        "price": round(float(price), 2),
        "prev" : round(float(prev), 2) if prev else None,
    }

def av_get_options_chain(ticker):
    """Fetch AV options chain; returns raw list or None. Used for finding BOTH legs same-source."""
    if ticker in ETF_LIST:
        return None
    today_str = datetime.date.today().isoformat()
    data = _av_get({"function": "HISTORICAL_OPTIONS", "symbol": ticker, "date": today_str})
    if not data:
        return None
    return data.get("data", []) or None

def _av_price_quality(bid, ask, last):
    """Determine price + quality from AV row. Returns dict like get_option_price_quality."""
    bid = float(bid or 0); ask = float(ask or 0); last = float(last or 0)
    if bid > 0 and ask > bid:
        return {"price": round((bid + ask) / 2, 2), "quality": "BID_ASK_MID",
                "bid": round(bid, 2), "ask": round(ask, 2)}
    if last > 0:
        return {"price": round(last, 2), "quality": "LAST_PRICE_ONLY",
                "bid": round(bid, 2), "ask": round(ask, 2)}
    return {"price": None, "quality": "MISSING", "bid": None, "ask": None}

def av_find_spread_legs(raw, target_delta, target_dte=TARGET_DTE):
    """
    From AV options data, find BOTH short put (near target_delta) AND long put
    (SPREAD_WIDTH below short) at the SAME expiry from the SAME source.
    Returns dict or None.
    """
    today = datetime.date.today()
    puts = [
        row for row in raw
        if row.get("type", "").lower() == "put"
        and row.get("expiration")
        and MIN_DTE <= (datetime.datetime.strptime(row["expiration"], "%Y-%m-%d").date() - today).days <= MAX_DTE
        and float(row.get("implied_volatility") or 0) > 0.01
    ]
    if not puts:
        return None
    def dte_dist(row):
        return abs((datetime.datetime.strptime(row["expiration"], "%Y-%m-%d").date() - today).days - target_dte)
    puts.sort(key=dte_dist)
    target_exp = puts[0]["expiration"]
    exp_date   = datetime.datetime.strptime(target_exp, "%Y-%m-%d").date()
    days_to_exp = (exp_date - today).days
    exp_puts = [row for row in puts if row["expiration"] == target_exp]
    # Short: closest to target_delta
    def delta_dist(row):
        return abs(abs(float(row.get("delta") or 0)) - target_delta)
    exp_puts.sort(key=delta_dist)
    short_row = exp_puts[0]
    short_strike = float(short_row["strike"])
    long_strike  = round(short_strike - SPREAD_WIDTH, 2)
    # Long: find row at long_strike in SAME source/expiry
    long_row = None
    for row in exp_puts:
        if abs(float(row["strike"]) - long_strike) < 0.01:
            long_row = row
            break
    short_pq = _av_price_quality(short_row.get("bid"), short_row.get("ask"), short_row.get("last"))
    long_pq  = _av_price_quality(long_row.get("bid"), long_row.get("ask"), long_row.get("last")) if long_row else {"price": None, "quality": "MISSING", "bid": None, "ask": None}
    return {
        "expiry"      : target_exp,
        "dte"         : days_to_exp,
        "short_strike": short_strike,
        "long_strike" : long_strike,
        "short_pq"    : short_pq,
        "long_pq"     : long_pq,
        "delta"       : round(abs(float(short_row.get("delta") or 0)), 3),
        "iv"          : round(float(short_row.get("implied_volatility", 0)) * 100 if float(short_row.get("implied_volatility", 0)) < 3 else float(short_row.get("implied_volatility", 0)), 1),
        "oi"          : int(float(short_row.get("open_interest") or 0)),
        "ba_width"    : round(short_pq["ask"] - short_pq["bid"], 2) if short_pq["ask"] and short_pq["bid"] and short_pq["ask"] > short_pq["bid"] else 999,
        "delta_method": "AV Greeks",
        "source"      : "AlphaVantage",
    }

# --- IV RANK SOURCES --------------------------------------------------
def get_ivr_barchart(ticker):
    """Scrape IVR from Barchart - the only source that allows TAKE_IT."""
    try:
        url = "https://www.barchart.com/stocks/quotes/" + ticker + "/overview"
        h = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"}
        r = requests.get(url, headers=h, timeout=12)
        if r.status_code != 200:
            return None
        m = re.search(r"ivRank.*?(\d+\.?\d*)", r.text)
        if m: return round(float(m.group(1)), 1)
        m = re.search(r"IV Rank[^\d]*(\d+\.?\d*)", r.text)
        if m: return round(float(m.group(1)), 1)
        return None
    except:
        return None

def get_iv_yfinance(ticker):
    """yfinance: price, HV, and IVR approximation (ESTIMATED quality)."""
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="60d")
        if hist.empty:
            return {}
        price = round(float(hist["Close"].iloc[-1]), 2)
        hv    = round(float(hist["Close"].pct_change().std()) * (252 ** 0.5) * 100, 1)
        opts  = tk.options
        if not opts:
            return {"price": price, "hv": hv}
        iv_list = []
        for exp in opts[:10]:
            try:
                puts = tk.option_chain(exp).puts
                atm  = puts[
                    (puts["strike"] >= price * 0.90) &
                    (puts["strike"] <= price * 1.10) &
                    (puts["impliedVolatility"] > 0.01)
                ]
                if atm.empty: continue
                iv_raw = float(atm["impliedVolatility"].median())
                iv_pct = round(iv_raw * 100 if iv_raw < 3 else iv_raw, 1)
                if 5 < iv_pct < 300:
                    iv_list.append(iv_pct)
            except:
                continue
        if not iv_list:
            return {"price": price, "hv": hv}
        cur_iv = iv_list[0]; iv_min = min(iv_list); iv_max = max(iv_list)
        if len(iv_list) >= 4 and iv_max > iv_min + 2:
            ivr = round(((cur_iv - iv_min) / (iv_max - iv_min)) * 100, 1)
        else:
            ivr = round(min(100, max(0, (cur_iv / hv - 0.7) * 125)), 1) if hv > 0 else 0
        return {
            "price": price, "iv": cur_iv, "hv": hv,
            "ivr": max(0, min(100, ivr)), "samples": len(iv_list),
        }
    except:
        return {}

def get_iv_data(ticker):
    """Unified IV data. Price: AV > yfinance. IVR: Barchart only confirmed; yfinance is ESTIMATED."""
    av_price  = av_get_price(ticker)
    price     = av_price["price"] if av_price else None
    price_src = "AV" if av_price else "yf"
    yfd = get_iv_yfinance(ticker)
    if price is None:
        price = yfd.get("price")
    iv  = yfd.get("iv", 0)
    hv  = yfd.get("hv", 0)
    bvr = get_ivr_barchart(ticker)
    if bvr is not None:
        ivr        = bvr
        ivr_source = "Barchart"
    else:
        ivr        = yfd.get("ivr", 0)
        ivr_source = "yfinance-estimated"
    print("  [" + price_src + "] $" + str(price) + " | IV:" + str(iv) + "% | HV:" + str(hv) + "% | IVR:" + str(ivr) + " [" + ivr_source + "]")
    return {"price": price, "iv": iv, "hv": hv, "ivr": ivr, "ivr_source": ivr_source}

def get_market():
    out = {}
    for t in ["SPY", "QQQ"]:
        pdata = av_get_price(t)
        if pdata and pdata.get("prev"):
            price = pdata["price"]; prev = pdata["prev"]
            pct = round((price - prev) / prev * 100, 2)
            out[t] = {"price": price, "pct": pct}
        else:
            try:
                h = yf.Ticker(t).history(period="5d")
                if len(h) >= 2:
                    p  = round(float(h["Close"].iloc[-1]), 2)
                    pc = round((p - float(h["Close"].iloc[-2])) / float(h["Close"].iloc[-2]) * 100, 2)
                    out[t] = {"price": p, "pct": pc}
            except:
                pass
    return out

# --- YFINANCE OPTION PRICE QUALITY ------------------------------------
def get_option_price_quality(ticker, exp_date, strike):
    """
    Returns dict:
      {"price": <float|None>, "quality": "BID_ASK_MID"|"LAST_PRICE_ONLY"|"MISSING",
       "bid": <float|None>, "ask": <float|None>}
    SAFETY RULE: Only BID_ASK_MID quality can support TAKE_IT.
    """
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        best_exp = None; best_diff = 999
        for exp in opts:
            try:
                ed   = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                diff = abs((ed - exp_date).days)
                if diff < best_diff:
                    best_diff = diff; best_exp = exp
            except:
                continue
        if not best_exp or best_diff > 7:
            return {"price": None, "quality": "MISSING", "bid": None, "ask": None}
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return {"price": None, "quality": "MISSING", "bid": None, "ask": None}
        row = puts.iloc[(puts["strike"] - strike).abs().argsort()[:1]]
        if row.empty:
            return {"price": None, "quality": "MISSING", "bid": None, "ask": None}
        bid = float(row["bid"].iloc[0])
        ask = float(row["ask"].iloc[0])
        lv  = float(row["lastPrice"].iloc[0])
        if bid > 0 and ask > 0 and ask > bid:
            return {"price": round((bid + ask) / 2, 2), "quality": "BID_ASK_MID",
                    "bid": round(bid, 2), "ask": round(ask, 2)}
        if lv > 0:
            return {"price": round(lv, 2), "quality": "LAST_PRICE_ONLY",
                    "bid": round(bid, 2) if bid > 0 else None,
                    "ask": round(ask, 2) if ask > 0 else None}
        return {"price": None, "quality": "MISSING", "bid": None, "ask": None}
    except:
        return {"price": None, "quality": "MISSING", "bid": None, "ask": None}


# --- STRIKE SELECTION (yfinance fallback) -----------------------------
def find_strike_by_delta_yf(ticker, exp_date, price, target_delta):
    """Pick put strike closest to target_delta. Returns (strike, delta_approx, method)."""
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if not opts:
            return None, None, None
        best_exp = min(opts, key=lambda e: abs((datetime.datetime.strptime(e, "%Y-%m-%d").date() - exp_date).days))
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return None, None, None
        candidates = puts[(puts["strike"] > price * 0.70) & (puts["strike"] < price * 0.97)].copy()
        if candidates.empty:
            return None, None, None
        otm_target = price * (1 - target_delta * 0.40)
        row = candidates.iloc[(candidates["strike"] - otm_target).abs().argsort()[:1]]
        if row.empty:
            return None, None, None
        strike = float(row["strike"].iloc[0])
        iv_val = float(row["impliedVolatility"].iloc[0]) if "impliedVolatility" in row.columns else 0
        if iv_val > 0:
            delta_approx = round(min(0.35, max(0.15,
                0.5 - (price - strike) / (price * iv_val * (TARGET_DTE / 365) ** 0.5 + 1e-9))), 3)
        else:
            delta_approx = 0.28
        return strike, delta_approx, "IV-approx"
    except Exception as e:
        print("  IV-approx error: " + str(e))
        return None, None, None

def check_liquidity(ticker, exp_date, strike, max_ba=MAX_BID_ASK_WIDTH):
    """Returns (passes, oi, spread, reason)."""
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if not opts:
            return False, 0, 0, "No option chain"
        best_exp = min(opts, key=lambda e: abs((datetime.datetime.strptime(e, "%Y-%m-%d").date() - exp_date).days))
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return False, 0, 0, "Empty puts chain"
        row = puts.iloc[(puts["strike"] - strike).abs().argsort()[:1]]
        if row.empty:
            return False, 0, 0, "Strike not found"
        bid    = float(row["bid"].iloc[0])
        ask    = float(row["ask"].iloc[0])
        oi     = int(row["openInterest"].iloc[0]) if "openInterest" in row.columns else 0
        spread = round(ask - bid, 2) if ask > bid else 0
        if oi < MIN_OPEN_INTEREST:
            return False, oi, spread, "OI " + str(oi) + " < " + str(MIN_OPEN_INTEREST) + " (low open interest)"
        if spread > max_ba:
            return False, oi, spread, "B/A $" + str(spread) + " > $" + str(max_ba) + " (spread too wide)"
        return True, oi, spread, "OK"
    except Exception as e:
        return False, 0, 0, "Liquidity check error: " + str(e)

def get_best_expiry_yf(ticker):
    """Find expiry closest to TARGET_DTE within MIN_DTE-MAX_DTE range."""
    try:
        tk    = yf.Ticker(ticker)
        opts  = tk.options
        today = datetime.date.today()
        best_exp = None; best_dte = None
        for exp in opts:
            try:
                ed  = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (ed - today).days
                if MIN_DTE <= dte <= MAX_DTE:
                    if best_dte is None or abs(dte - TARGET_DTE) < abs(best_dte - TARGET_DTE):
                        best_exp = exp; best_dte = dte
            except:
                continue
        return best_exp, best_dte
    except:
        return None, None

# --- VERDICT HELPERS --------------------------------------------------
def _downgrade(r, reason, data_quality="MISSING"):
    """Downgrade a result to MANUAL_CHECK with reason."""
    r["verdict"]      = "MANUAL_CHECK"
    r["reason"]       = reason
    r["data_quality"] = data_quality
    return r

def _skip(r, reason):
    """Mark a result as SKIP."""
    r["verdict"] = "SKIP"
    r["reason"]  = reason
    return r


# --- CORE SCANNER -----------------------------------------------------
def scan_ticker(ticker, vix, market_trend_status="BULLISH"):
    """
    Scans one ticker. Returns result dict with verdict.
    Verdicts: TAKE_IT | MANUAL_CHECK | SKIP
    TAKE_IT requires ALL of the following:
      - market_trend_status == BULLISH (not UNKNOWN, not BEARISH)
      - ticker trend == BULLISH (above 50MA)
      - IVR source == Barchart (not yfinance-estimated, even for ETFs)
      - Earnings == CONFIRMED or ETF (not UNKNOWN for stocks)
      - Both legs price_quality == BID_ASK_MID
      - Both legs same price source (no mixed AV + yfinance)
      - Risk <= 2% of account
      - Delta known and <= 0.35
      - High-risk stocks: IVR >= 50, earnings CONFIRMED, B/A <= 0.30
    """
    r = {"ticker": ticker, "verdict": "SKIP", "reason": "", "data_quality": ""}
    cat = ticker_category(ticker)
    r["category"] = cat

    # Step 1 - Price & IV
    print("  Getting IV data...")
    d     = get_iv_data(ticker)
    price = d.get("price")
    if not price:
        return _skip(r, "No price data")
    iv      = d.get("iv", 0)
    hv      = d.get("hv", 0)
    ivr     = d.get("ivr", 0)
    ivr_src = d.get("ivr_source", "unknown")
    r.update({"price": price, "iv": iv, "hv": hv, "ivr": ivr, "ivr_source": ivr_src})

    # Step 2 - Market trend gate
    if market_trend_status == "BEARISH":
        return _skip(r, "Market trend bearish (SPY+QQQ both below 50MA) - no new bull spreads")

    # Step 3 - Ticker trend
    trend_status, trend_detail = check_ticker_trend(ticker, price)
    r["trend"]        = trend_status + " | " + trend_detail
    r["trend_status"] = trend_status
    print("  Trend: " + trend_status + " | " + trend_detail)

    # Step 4 - Earnings gate
    safe_earn, days_e, date_e, earn_status = check_earnings(ticker)
    r["earnings"]        = date_e + " (" + str(days_e) + "d)"
    r["earnings_status"] = earn_status
    print("  Earnings: " + date_e + " (" + str(days_e) + "d) [" + earn_status + "]")
    if not safe_earn and earn_status == "CONFIRMED":
        return _skip(r, "Earnings in " + str(days_e) + "d - too close")

    # Step 5 - IVR gate
    effective_min_ivr = HR_MIN_IV_RANK if cat == "HIGH_RISK" else MIN_IV_RANK
    if ivr < effective_min_ivr:
        return _skip(r, "IVR " + str(ivr) + " < " + str(effective_min_ivr) + " (premium too cheap)")
    print("  IVR " + str(ivr) + " passes gate")

    # IVR tier -> target delta + size modifier
    if ivr >= 50:
        ivr_label = "Strong (>=50) - full size"
        ivr_mod   = 1.0
        tgt_delta = 0.30
    else:
        ivr_label = "Acceptable (30-50) - reduce size"
        ivr_mod   = 0.5
        tgt_delta = 0.25
    r["ivr_label"] = ivr_label

    # VIX modifier
    vix_mod  = vix_size_modifier(vix or 0)
    size_mod = ivr_mod * vix_mod
    if size_mod == 0:
        return _skip(r, "VIX > 30 - stand aside")

    # Step 6 - Get expiry & strikes (same-source spread legs)
    effective_max_ba = HR_MAX_BID_ASK_WIDTH if cat == "HIGH_RISK" else MAX_BID_ASK_WIDTH
    short_price_source = None; long_price_source = None; credit_source = None
    options_src = None; delta_used = None; delta_method = None
    oi = 0; ba_spread = 999
    short_pq = {"price": None, "quality": "MISSING", "bid": None, "ask": None}
    long_pq  = {"price": None, "quality": "MISSING", "bid": None, "ask": None}
    exp_str = None; exp_date = None; exp_disp = None; dte = None
    ss = None; ls = None

    # Try Alpha Vantage first (for stocks; ETFs skipped to conserve quota)
    av_raw = av_get_options_chain(ticker)
    av_spread = av_find_spread_legs(av_raw, tgt_delta) if av_raw else None

    if av_spread:
        options_src       = "AV"
        exp_str           = av_spread["expiry"]
        dte               = av_spread["dte"]
        ss                = av_spread["short_strike"]
        ls                = av_spread["long_strike"]
        short_pq          = av_spread["short_pq"]
        long_pq           = av_spread["long_pq"]
        delta_used        = av_spread["delta"]
        delta_method      = av_spread["delta_method"]
        oi                = av_spread["oi"]
        ba_spread         = av_spread["ba_width"]
        short_price_source = "AV"
        long_price_source  = "AV" if long_pq["price"] is not None else None
        exp_date          = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
        exp_disp          = exp_date.strftime("%b %d %Y")
        print("  [AV] " + exp_disp + " (" + str(dte) + "DTE) | $" + str(ss) + " delta:" + str(delta_used) + " | OI:" + str(oi))

        # Liquidity gates
        if oi < MIN_OPEN_INTEREST:
            return _skip(r, "Low open interest: OI " + str(oi) + " < " + str(MIN_OPEN_INTEREST))
        if ba_spread > effective_max_ba:
            return _skip(r, "Spread too wide: B/A $" + str(ba_spread) + " > $" + str(effective_max_ba))
        if delta_used and delta_used > TARGET_DELTA_HIGH:
            return _skip(r, "Delta " + str(delta_used) + " > " + str(TARGET_DELTA_HIGH) + " - too close to ATM")

        # If long leg missing from AV chain, try yfinance fallback BUT then credit becomes mixed-source
        # which BLOCKS TAKE_IT (forced to MANUAL_CHECK).
        if long_pq["price"] is None or long_pq["quality"] == "MISSING":
            fb = get_option_price_quality(ticker, exp_date, ls)
            if fb["price"] is not None:
                long_pq = fb
                long_price_source = "yfinance"
    else:
        # yfinance fallback path
        options_src = "yfinance"
        exp_str, dte = get_best_expiry_yf(ticker)
        if not exp_str:
            return _skip(r, "No expiry " + str(MIN_DTE) + "-" + str(MAX_DTE) + "DTE found")
        exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
        exp_disp = exp_date.strftime("%b %d %Y")

        # Delta-based strike selection
        print("  Finding delta-based short strike (~0.30 delta)...")
        ss, delta_used, delta_method = find_strike_by_delta_yf(ticker, exp_date, price, tgt_delta)
        if ss is None:
            ss = round(price * 0.88 / 2.5) * 2.5
            delta_used   = None
            delta_method = "Fixed-OTM (12%) - verify delta in broker"
        ls = ss - SPREAD_WIDTH
        print("  [yf] " + exp_disp + " (" + str(dte) + "DTE) | $" + str(ss) + " via " + str(delta_method))

        # Liquidity check
        print("  Checking liquidity...")
        liq_ok, oi, ba_spread, liq_reason = check_liquidity(ticker, exp_date, ss, effective_max_ba)
        if not liq_ok:
            return _skip(r, "Liquidity fail: " + liq_reason)
        print("  Liquidity OK | OI:" + str(oi) + " | B/A:$" + str(ba_spread))

        short_pq = get_option_price_quality(ticker, exp_date, ss)
        long_pq  = get_option_price_quality(ticker, exp_date, ls)
        short_price_source = "yfinance"
        long_price_source  = "yfinance" if long_pq["price"] is not None else None
        print("  Short put: $" + str(short_pq["price"]) + " [" + short_pq["quality"] + "]")
        print("  Long put : $" + str(long_pq["price"]) + " [" + long_pq["quality"] + "]")

    # Step 7 - Credit + price quality verification
    sm = short_pq["price"]; lm = long_pq["price"]

    # Hard SKIP if short leg missing entirely
    if sm is None:
        return _skip(r, "Could not verify short put price - check broker manually")

    # Determine credit_source: same source if both equal, else MIXED
    if short_price_source and long_price_source and short_price_source == long_price_source:
        credit_source = short_price_source
    elif short_price_source and long_price_source:
        credit_source = "MIXED"
    else:
        credit_source = short_price_source or "UNKNOWN"

    # If long missing - calculation impossible, MANUAL_CHECK
    if lm is None:
        credit = None
        r.update({"expiry": exp_disp, "dte": dte, "short_strike": ss, "long_strike": ls,
                  "delta": delta_used, "delta_method": delta_method, "options_src": options_src,
                  "open_interest": oi, "bid_ask": ba_spread, "credit": None,
                  "short_price_source": short_price_source, "long_price_source": long_price_source,
                  "credit_source": credit_source,
                  "price_quality": short_pq["quality"] + "/" + long_pq["quality"]})
        return _downgrade(r, "Could not verify long leg price - check live option chain manually", "MISSING")

    if sm <= lm:
        return _skip(r, "Invalid credit (short <= long price) - check broker manually")

    credit = round(sm - lm, 2)

    # Determine combined price_quality (worst of the two)
    def _worst_quality(a, b):
        order = {"BID_ASK_MID": 0, "LAST_PRICE_ONLY": 1, "MISSING": 2}
        return a if order.get(a, 9) >= order.get(b, 9) else b
    combined_quality = _worst_quality(short_pq["quality"], long_pq["quality"])

    # Update result with option details
    r.update({
        "expiry"            : exp_disp,
        "dte"               : dte,
        "short_strike"      : ss,
        "long_strike"       : ls,
        "delta"             : delta_used,
        "delta_method"      : delta_method,
        "open_interest"     : oi,
        "bid_ask"           : ba_spread,
        "options_src"       : options_src,
        "credit"            : credit,
        "short_price_source": short_price_source,
        "long_price_source" : long_price_source,
        "credit_source"     : credit_source,
        "price_quality"     : combined_quality,
    })
    print("  Credit: $" + str(credit) + " (source: " + str(credit_source) + ", quality: " + combined_quality + ")")

    # Credit minimum
    min_credit = round(SPREAD_WIDTH * MIN_CREDIT_RATIO, 2)
    if credit < min_credit:
        return _skip(r, "Credit $" + str(credit) + " < minimum $" + str(min_credit) + " (1/3 of width)")

    # Step 8 - Risk metrics & sizing
    max_loss_per_contract = (SPREAD_WIDTH - credit) * 100
    contracts = max(1, size_contracts(max_loss_per_contract, size_mod))
    m = calc_metrics(credit, SPREAD_WIDTH, contracts)
    risk_pct = round(m["nl_usd"] / ACCOUNT_SIZE_USD * 100, 1)

    if risk_pct <= 2:
        risk_warn = "OK: Within 2% rule (" + str(risk_pct) + "% of account)"
    elif risk_pct <= 3:
        risk_warn = "BORDERLINE: 2-3% risk (" + str(risk_pct) + "%) - reduce size"
    else:
        risk_warn = "EXCEEDS LIMIT: " + str(risk_pct) + "% - do not place"

    # Fill financial details (used by all branches below)
    r.update({
        "contracts" : contracts,
        "np"        : m["np_usd"],
        "np_rm"     : m["np_rm"],
        "nl"        : m["nl_usd"],
        "nl_rm"     : m["nl_rm"],
        "fees"      : m["fees"],
        "pop"       : m["pop"],
        "be"        : round(ss - credit, 2),
        "risk_pct"  : risk_pct,
        "risk_warn" : risk_warn,
        "size_note" : "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x",
    })

    # Step 9 - Apply downgrade rules (in order; first match wins)
    # Each rule returns MANUAL_CHECK if triggered.

    # 9a - Risk > 3% = SKIP
    if risk_pct > 3.0:
        return _skip(r, "Risk " + str(risk_pct) + "% exceeds 3% limit - SKIP")

    # 9b - Risk > 2% = MANUAL_CHECK
    if risk_pct > 2.0:
        return _downgrade(r, "Risk " + str(risk_pct) + "% exceeds 2% - reduce size before placing", "VERIFIED")

    # 9c - Market trend UNKNOWN = MANUAL_CHECK (cannot confirm bullish bias)
    if market_trend_status == "UNKNOWN":
        return _downgrade(r, "Market trend data unavailable - cannot confirm bullish bias", "MISSING")

    # 9d - Ticker trend not BULLISH = MANUAL_CHECK or SKIP
    if trend_status == "BEARISH":
        return _downgrade(r, "Ticker trend not strong enough for automatic TAKE_IT (below 200MA)", "VERIFIED")
    if trend_status == "CAUTION":
        return _downgrade(r, "Ticker trend not strong enough for automatic TAKE_IT (below 50MA)", "VERIFIED")
    if trend_status == "UNKNOWN":
        return _downgrade(r, "Ticker trend not strong enough for automatic TAKE_IT (data unknown)", "MISSING")

    # 9e - IVR estimated (not Barchart) = MANUAL_CHECK for ALL tickers including ETFs
    if ivr_src != "Barchart":
        return _downgrade(r, "IVR is estimated (not from Barchart) - confirm IVR before placing", "ESTIMATED")

    # 9f - Earnings UNKNOWN for non-ETF = MANUAL_CHECK
    if earn_status == "UNKNOWN" and cat != "ETF":
        return _downgrade(r, "Earnings date unknown for " + ticker + " - verify no earnings within 14 days", "MISSING")

    # 9g - Delta unknown = MANUAL_CHECK
    if delta_used is None:
        return _downgrade(r, "Delta unknown - verify strike in broker before placing", "ESTIMATED")

    # 9h - Price quality not BID_ASK_MID on either leg = MANUAL_CHECK
    if short_pq["quality"] != "BID_ASK_MID":
        return _downgrade(r, "Short leg price quality is " + short_pq["quality"] + " (lastPrice may be stale) - verify in broker", "ESTIMATED")
    if long_pq["quality"] != "BID_ASK_MID":
        return _downgrade(r, "Long leg price quality is " + long_pq["quality"] + " (lastPrice may be stale) - verify in broker", "ESTIMATED")

    # 9i - Mixed-source spread credit = MANUAL_CHECK
    if credit_source == "MIXED":
        return _downgrade(r, "Spread credit uses mixed sources (" + str(short_price_source) + " short + " + str(long_price_source) + " long) - verify in broker", "ESTIMATED")

    # 9j - High-risk stocks need IVR >= 50 AND CONFIRMED earnings
    if cat == "HIGH_RISK":
        if ivr < HR_MIN_IV_RANK:
            return _downgrade(r, "High-risk stock: IVR " + str(ivr) + " < 50", "VERIFIED")
        if earn_status != "CONFIRMED":
            return _downgrade(r, "High-risk stock: earnings not confirmed - verify before placing", "MISSING")

    # ALL checks passed -> TAKE_IT
    r["verdict"]      = "TAKE_IT"
    r["data_quality"] = "VERIFIED"
    print("  TAKE_IT | Credit:$" + str(credit) + " | " + str(contracts) + "x | Loss:$" + str(m["nl_usd"]) + " | PoP:" + str(m["pop"]) + "% | " + risk_warn)
    return r

# --- MESSAGE FORMATTERS -----------------------------------------------
def fmt_market(vix, mkt, market_trend_status, market_trend_reason):
    spy = mkt.get("SPY", {})
    qqq = mkt.get("QQQ", {})
    vix_warn = "\n  VIX elevated - position sizes reduced 50%" if vix and vix >= 25 else ""
    return (
        "BILLY SCANNER - " + str(datetime.date.today()) + "\n"
        + "================================\n"
        + "SPY: $" + str(spy.get("price","?")) + " (" + str(spy.get("pct",0)) + "%)\n"
        + "QQQ: $" + str(qqq.get("price","?")) + " (" + str(qqq.get("pct",0)) + "%)\n"
        + "VIX: " + str(vix) + " - " + vix_label(vix) + vix_warn + "\n"
        + "Trend [" + market_trend_status + "]: " + market_trend_reason + "\n"
        + "Account: $" + str(ACCOUNT_SIZE_USD) + " USD | Max risk/trade: $" + str(MAX_RISK_USD) + "\n"
        + "================================"
    )

def fmt_verdict_icon(verdict):
    if verdict == "TAKE_IT":      return "[POSSIBLE SETUP]"
    if verdict == "MANUAL_CHECK": return "[MANUAL CHECK]"
    return "[SKIP]"

def fmt_trade(r):
    v       = r.get("verdict", "SKIP")
    icon    = fmt_verdict_icon(v)
    dq      = r.get("data_quality", "UNKNOWN")
    delta_s = str(round(r["delta"], 3)) if r.get("delta") else "~0.28 (approx - verify)"
    trend_s = r.get("trend", "Unknown")
    earn_s  = r.get("earnings", "?") + " [" + r.get("earnings_status", "?") + "]"
    credit  = r.get("credit", 0) or 0
    src_note = "Data: " + str(r.get("options_src","?")) + " | IVR: " + str(r.get("ivr_source","?")) + " | Credit src: " + str(r.get("credit_source","?")) + " | Price q: " + str(r.get("price_quality","?"))

    lines = [
        icon + " " + r["ticker"] + " - " + v,
        "================================",
        "Data quality: " + dq,
    ]
    if r.get("reason"):
        lines.append("Reason: " + r["reason"])
    lines += [
        "--------------------------------",
        "Ticker  : " + r["ticker"] + " (" + r.get("category","?") + ")",
        "Expiry  : " + str(r.get("expiry","?")) + " (" + str(r.get("dte","?")) + " DTE)",
        "Short   : $" + str(r.get("short_strike","?")) + " Put (delta " + delta_s + ")",
        "Long    : $" + str(r.get("long_strike","?")) + " Put",
        "Credit  : $" + str(credit),
        "Max profit: $" + str(r.get("np","?")) + " / RM" + str(r.get("np_rm","?")),
        "Max loss  : $" + str(r.get("nl","?")) + " / RM" + str(r.get("nl_rm","?")),
        "Risk %    : " + str(r.get("risk_pct","?")) + "% - " + r.get("risk_warn","?"),
        "B/Even    : $" + str(r.get("be","?")) + " | PoP: " + str(r.get("pop","?")) + "%",
        "OI      : " + str(r.get("open_interest","?")) + " | B/A: $" + str(r.get("bid_ask","?")),
        "IVR     : " + str(r.get("ivr","?")) + " [" + r.get("ivr_source","?") + "] - " + r.get("ivr_label","?"),
        "Trend   : " + trend_s,
        "Earnings: " + earn_s,
        "Source  : " + src_note,
        "--------------------------------",
    ]
    if v == "TAKE_IT":
        lines += [
            "MANAGEMENT",
            "Take profit: $" + str(round(credit/2,2)) + " debit (50% of credit)",
            "Stop loss  : $" + str(round(credit*2,2)) + " debit (2x credit)",
            "Close by   : 21 DTE (gamma risk)",
            "Exit if    : price breaks below $" + str(r.get("short_strike","?")),
        ]
    lines += [
        "================================",
        "Possible setup found. Verify in",
        "broker before placing any trade.",
        "This is not financial advice.",
    ]
    return "\n".join(lines)

def fmt_skip(r):
    return (
        "[SKIP] " + r["ticker"] + "\n"
        + "Reason: " + r["reason"] + "\n"
        + "IVR: " + str(r.get("ivr",0)) + " | Earnings: " + r.get("earnings","?")
    )

def fmt_summary(results, vix, market_trend_status):
    takes   = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    manuals = [r["ticker"] for r in results if r["verdict"] == "MANUAL_CHECK"]
    skips   = [r["ticker"] for r in results if r["verdict"] == "SKIP"]
    warn = "\n  VIX " + str(vix) + " elevated - reduce all sizes" if vix and vix > 25 else ""
    if market_trend_status == "BEARISH":
        trend_warn = "\n  Market trend bearish - no TAKE_IT signals today"
    elif market_trend_status == "UNKNOWN":
        trend_warn = "\n  Market trend unknown - all setups downgraded to MANUAL_CHECK"
    else:
        trend_warn = ""
    return (
        "SCAN SUMMARY\n"
        + "================================\n"
        + "Scanned     : " + str(len(results)) + "/" + str(len(WATCHLIST)) + "\n"
        + "TAKE_IT     : " + (", ".join(takes) if takes else "None today") + "\n"
        + "MANUAL_CHECK: " + (", ".join(manuals) if manuals else "None") + "\n"
        + "Skipped     : " + str(len(skips)) + warn + trend_warn + "\n"
        + "AV calls    : " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT) + "\n"
        + "================================\n"
        + "Always verify before placing:\n"
        + "  IVR confirmed via Barchart (not yfinance)\n"
        + "  Delta ~0.30 (max 0.35) via live broker Greeks\n"
        + "  OI >= " + str(MIN_OPEN_INTEREST) + " | B/A <= $" + str(MAX_BID_ASK_WIDTH) + "\n"
        + "  No earnings within " + str(EARNINGS_BUFFER) + " days\n"
        + "  Risk <= 2% of account ($" + str(MAX_RISK_USD) + ")\n"
        + "  This scanner is for screening only.\n"
        + "  Not financial advice."
    )

# --- JOURNAL / OUTPUT -------------------------------------------------
JOURNAL_FIELDS = [
    "date","ticker","verdict","reason","data_quality","category",
    "price","iv","ivr","ivr_source","trend","earnings","earnings_status",
    "expiry","dte","short_strike","long_strike","credit",
    "short_price_source","long_price_source","credit_source","price_quality",
    "open_interest","bid_ask","risk_pct","max_profit","max_loss","contracts",
]

def _journal_row(r):
    """Flatten a result dict into a journal row."""
    return {
        "date"              : str(datetime.date.today()),
        "ticker"            : r.get("ticker"),
        "verdict"           : r.get("verdict"),
        "reason"            : r.get("reason"),
        "data_quality"      : r.get("data_quality"),
        "category"          : r.get("category"),
        "price"             : r.get("price"),
        "iv"                : r.get("iv"),
        "ivr"               : r.get("ivr"),
        "ivr_source"        : r.get("ivr_source"),
        "trend"             : r.get("trend"),
        "earnings"          : r.get("earnings"),
        "earnings_status"   : r.get("earnings_status"),
        "expiry"            : r.get("expiry"),
        "dte"               : r.get("dte"),
        "short_strike"      : r.get("short_strike"),
        "long_strike"       : r.get("long_strike"),
        "credit"            : r.get("credit"),
        "short_price_source": r.get("short_price_source"),
        "long_price_source" : r.get("long_price_source"),
        "credit_source"     : r.get("credit_source"),
        "price_quality"     : r.get("price_quality"),
        "open_interest"     : r.get("open_interest"),
        "bid_ask"           : r.get("bid_ask"),
        "risk_pct"          : r.get("risk_pct"),
        "max_profit"        : r.get("np"),
        "max_loss"          : r.get("nl"),
        "contracts"         : r.get("contracts"),
    }

def write_journal(results):
    """Write scan results to output/scan_results_YYYY-MM-DD.{json,csv}."""
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        today_str = datetime.date.today().isoformat()
        rows = [_journal_row(r) for r in results]
        # JSON
        json_path = os.path.join(OUTPUT_DIR, "scan_results_" + today_str + ".json")
        with open(json_path, "w") as f:
            json.dump(rows, f, indent=2, default=str)
        # CSV
        csv_path = os.path.join(OUTPUT_DIR, "scan_results_" + today_str + ".csv")
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            w.writeheader()
            for row in rows:
                w.writerow(row)
        print("  Journal written: " + json_path + " and " + csv_path)
    except Exception as e:
        print("  Journal write error: " + str(e))


# --- MAIN -------------------------------------------------------------
def run():
    now = datetime.datetime.utcnow()
    print("=" * 55)
    print("BILLY OPTIONS SCANNER - tastytrade framework")
    print(now.strftime("%Y-%m-%d %H:%M") + " UTC")
    print("Account: $" + str(ACCOUNT_SIZE_USD) + " USD | Max risk/trade: $" + str(MAX_RISK_USD) + " (2%)")
    print("AV key: " + ("configured" if AV_API_KEY else "MISSING - set AV_API_KEY secret"))
    print("Watchlist: " + str(len(WATCHLIST)) + " tickers")
    print("=" * 55)

    send_telegram(
        "Billy Scanner Starting\n"
        + now.strftime("%Y-%m-%d %H:%M") + " UTC\n"
        + "Account: $" + str(ACCOUNT_SIZE_USD) + " USD | Risk limit: $" + str(MAX_RISK_USD) + "/trade\n"
        + "Scanning " + str(len(WATCHLIST)) + " tickers: " + ", ".join(WATCHLIST)
    )

    vix = get_vix()
    mkt = get_market()
    print("VIX: " + str(vix) + " - " + vix_label(vix))

    # VIX > 30 halt
    if vix and vix > 30:
        msg = (
            "VIX ALERT: " + str(vix) + "\n"
            + "VIX > 30 = High Fear\n"
            + "Scanner halted - stand aside today.\n"
            + "Gap risk overwhelms statistical edge."
        )
        print(msg)
        send_telegram(msg)
        return

    # Market trend check
    market_trend_status, market_trend_reason = check_market_trend()
    print("Market trend: [" + market_trend_status + "] " + market_trend_reason)
    send_telegram(fmt_market(vix, mkt, market_trend_status, market_trend_reason))

    # Portfolio exposure counters
    take_it_count        = 0
    high_risk_take_count = 0
    results = []

    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print("\n[" + str(i) + "/" + str(len(WATCHLIST)) + "] " + ticker + " (AV: " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT) + ")")
            r = scan_ticker(ticker, vix, market_trend_status)

            # Portfolio exposure limits - cap TAKE_IT alerts per run
            if r["verdict"] == "TAKE_IT":
                if take_it_count >= MAX_TRADES_PER_SCAN:
                    r["verdict"] = "MANUAL_CHECK"
                    r["reason"]  = "Trade limit reached - avoid overexposure (max " + str(MAX_TRADES_PER_SCAN) + " per scan)"
                elif r.get("category") == "HIGH_RISK" and high_risk_take_count >= MAX_HIGH_RISK_STOCK_TRADES:
                    r["verdict"] = "MANUAL_CHECK"
                    r["reason"]  = "High-risk stock limit reached (max " + str(MAX_HIGH_RISK_STOCK_TRADES) + " per scan)"
                else:
                    take_it_count += 1
                    if r.get("category") == "HIGH_RISK":
                        high_risk_take_count += 1

            results.append(r)

            if r["verdict"] in ("TAKE_IT", "MANUAL_CHECK"):
                send_telegram(fmt_trade(r))
            else:
                send_telegram(fmt_skip(r))

            time.sleep(2)
        except Exception as e:
            print("  Error scanning " + ticker + ": " + str(e))
            continue

    # Write journal artifact
    write_journal(results)

    send_telegram(fmt_summary(results, vix, market_trend_status))
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    print("\nDONE | Trades found: " + str(takes or "None") + " | AV: " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT))


if __name__ == "__main__":
    run()
