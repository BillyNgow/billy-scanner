#!/usr/bin/env python3
"""
Billy Options Scanner - Cloud Version (GitHub Actions)
Framework: Tom Sosnoff / tastytrade bull put spread

Data sources (priority order):
  1. Alpha Vantage - price (GLOBAL_QUOTE) + options (HISTORICAL_OPTIONS)
  2. yfinance      - HV, IVR calc, options chain fallback, VIX, earnings
  3. Barchart      - IVR scrape (most reliable IVR source)

No IBKR / TWS required. Runs headless on GitHub Actions.
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

import os, re, datetime, time, math, warnings
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd

# --- CONFIG -----------------------------------------------------------
# Credentials - from GitHub Secrets (never hardcode)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
AV_API_KEY       = os.environ.get("AV_API_KEY", "")

# Account
ACCOUNT_SIZE_USD      = 25000
MAX_RISK_PCT          = 0.02
MAX_RISK_USD          = ACCOUNT_SIZE_USD * MAX_RISK_PCT
USD_MYR_RATE          = 4.40

# Portfolio exposure limits
MAX_TOTAL_OPEN_RISK_PCT    = 0.06
MAX_TRADES_PER_SCAN        = 2
MAX_HIGH_RISK_STOCK_TRADES = 1

# Ticker classification
ETF_LIST = [
    "SPY","QQQ","IWM","DIA","GLD","TLT","USO","SLV",
    "EEM","XLE","XLF","FXI","ARKK","SOXX"
]
HIGH_RISK_STOCKS = ["TSLA","NVDA","COIN","MSTR","PLTR"]
NORMAL_STOCKS    = ["AAPL","AMD","META","AMZN"]

# Watchlist (ETFs first)
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
MIN_OPEN_INTEREST = 50
MAX_BID_ASK_WIDTH = 0.50

# High-risk stock stricter rules
HR_MIN_IV_RANK        = 50
HR_MAX_BID_ASK_WIDTH  = 0.30

# Alpha Vantage
AV_BASE       = "https://www.alphavantage.co/query"
AV_CALL_COUNT = 0
AV_FREE_LIMIT = 25

# --- TELEGRAM ----------------------------------------------------------
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


# --- FEE & METRIC HELPERS ----------------------------------------------
def calc_fees(contracts=1):
    return round(IBKR_FEE * 2 * 2 * contracts, 2)

def calc_metrics(credit, width, contracts=1):
    gross_profit = credit * 100 * contracts
    gross_loss   = (width - credit) * 100 * contracts
    fees = calc_fees(contracts)
    pop  = round((1 - credit / width) * 100, 1) if width > 0 else 0
    return {
        "np_usd": round(gross_profit - fees, 2),
        "nl_usd": round(gross_loss + fees, 2),
        "np_rm" : round((gross_profit - fees) * USD_MYR_RATE, 2),
        "nl_rm" : round((gross_loss + fees) * USD_MYR_RATE, 2),
        "fees"  : round(fees, 2),
        "pop"   : pop,
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

# --- VIX HELPERS -------------------------------------------------------
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


# --- TREND FILTER ------------------------------------------------------
def get_moving_averages(ticker):
    """Return dict with price, ma20, ma50, ma200 or None on failure."""
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
    Returns (bullish: bool, reason: str).
    If both SPY and QQQ are below their 50-day MA, return bullish=False.
    """
    spy = get_moving_averages("SPY")
    qqq = get_moving_averages("QQQ")
    if spy is None or qqq is None:
        return True, "Trend data unavailable - assuming neutral"
    spy_above = spy["price"] >= spy["ma50"]
    qqq_above = qqq["price"] >= qqq["ma50"]
    if not spy_above and not qqq_above:
        return False, (
            "SPY $" + str(spy["price"]) + " below 50MA $" + str(spy["ma50"]) + " AND "
            + "QQQ $" + str(qqq["price"]) + " below 50MA $" + str(qqq["ma50"]) + " - bearish trend"
        )
    return True, (
        "SPY $" + str(spy["price"]) + " vs 50MA $" + str(spy["ma50"]) + " | "
        + "QQQ $" + str(qqq["price"]) + " vs 50MA $" + str(qqq["ma50"])
    )

def check_ticker_trend(ticker, price):
    """
    Returns (status: str, detail: str).
    status: BULLISH, CAUTION, BEARISH, or UNKNOWN
    """
    ma = get_moving_averages(ticker)
    if ma is None:
        return "UNKNOWN", "Could not fetch moving averages"
    above_50  = price >= ma["ma50"]
    above_200 = (price >= ma["ma200"]) if ma["ma200"] else None
    detail = "Price $" + str(price) + " | 50MA $" + str(ma["ma50"]) + " | 200MA $" + str(ma["ma200"] or "N/A")
    if above_200 is False:
        return "BEARISH", "Below 200MA - " + detail
    if not above_50:
        return "CAUTION", "Below 50MA - " + detail
    return "BULLISH", detail

# --- EARNINGS ----------------------------------------------------------
def check_earnings(ticker):
    """
    Returns (safe: bool, days: int, date_str: str, status: str).
    status: ETF | CONFIRMED | UNKNOWN
    ETFs are always safe. Unknown earnings = MANUAL_CHECK for stocks.
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


# --- ALPHA VANTAGE HELPERS ---------------------------------------------
def _av_get(params):
    """Single Alpha Vantage API call with quota guard. Returns JSON or None."""
    global AV_CALL_COUNT
    if AV_CALL_COUNT >= AV_FREE_LIMIT:
        print("  [AV quota " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT) + " reached]")
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

def av_get_options(ticker, target_delta=TARGET_DELTA):
    """
    AV HISTORICAL_OPTIONS -> put closest to target_delta at ~45 DTE.
    Returns option dict or None.
    """
    if ticker in ETF_LIST:
        return None
    today_str = datetime.date.today().isoformat()
    data = _av_get({"function": "HISTORICAL_OPTIONS", "symbol": ticker, "date": today_str})
    if not data:
        return None
    raw = data.get("data", [])
    if not raw:
        return None
    today = datetime.date.today()
    puts = [
        c for c in raw
        if c.get("type", "").lower() == "put"
        and c.get("expiration")
        and MIN_DTE <= (datetime.datetime.strptime(c["expiration"], "%Y-%m-%d").date() - today).days <= MAX_DTE
        and float(c.get("implied_volatility") or 0) > 0.01
    ]
    if not puts:
        return None
    def dte_dist(c):
        return abs((datetime.datetime.strptime(c["expiration"], "%Y-%m-%d").date() - today).days - TARGET_DTE)
    puts.sort(key=dte_dist)
    target_exp = puts[0]["expiration"]
    exp_date   = datetime.datetime.strptime(target_exp, "%Y-%m-%d").date()
    days_to_exp = (exp_date - today).days
    exp_puts = [c for c in puts if c["expiration"] == target_exp]
    def delta_dist(c):
        return abs(abs(float(c.get("delta") or 0)) - target_delta)
    exp_puts.sort(key=delta_dist)
    chosen = exp_puts[0]
    short_strike = float(chosen["strike"])
    long_strike  = round(short_strike - SPREAD_WIDTH, 2)
    iv_raw  = float(chosen.get("implied_volatility", 0))
    iv_pct  = round(iv_raw * 100 if iv_raw < 3 else iv_raw, 1)
    delta_val = round(abs(float(chosen.get("delta") or 0)), 3)
    bid = float(chosen.get("bid") or 0)
    ask = float(chosen.get("ask") or 0)
    lp  = float(chosen.get("last") or 0)
    oi  = int(float(chosen.get("open_interest") or 0))
    ba_width  = round(ask - bid, 2) if ask > bid else 999
    short_mid = round((bid + ask) / 2, 2) if bid > 0 and ask > bid else round(lp, 2)
    return {
        "iv"          : iv_pct,
        "expiry"      : target_exp,
        "dte"         : days_to_exp,
        "short_strike": short_strike,
        "long_strike" : long_strike,
        "short_mid"   : short_mid,
        "delta"       : delta_val,
        "oi"          : oi,
        "ba_width"    : ba_width,
        "delta_method": "AV Greeks",
        "source"      : "AlphaVantage",
    }

# --- IV RANK SOURCES ---------------------------------------------------
def get_ivr_barchart(ticker):
    """Scrape IVR from Barchart - most reliable free IVR source."""
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
    """yfinance: price, HV, and IVR approximation from options chain."""
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
    """Unified IV data: Price from AV > yfinance. IVR from Barchart > yfinance."""
    av_price  = av_get_price(ticker)
    price     = av_price["price"] if av_price else None
    price_src = "AV" if av_price else "yf"
    yfd = get_iv_yfinance(ticker)
    if price is None:
        price = yfd.get("price")
    iv  = yfd.get("iv", 0)
    hv  = yfd.get("hv", 0)
    bvr = get_ivr_barchart(ticker)
    ivr = bvr if bvr is not None else yfd.get("ivr", 0)
    ivr_src = "Barchart" if bvr is not None else "yfinance-estimated"
    print("  [" + price_src + "] $" + str(price) + " | IV:" + str(iv) + "% | HV:" + str(hv) + "% | IVR:" + str(ivr) + " [" + ivr_src + "]")
    return {
        "price": price, "iv": iv, "hv": hv,
        "ivr": ivr, "ivr_source": ivr_src,
    }

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

# --- STRIKE SELECTION --------------------------------------------------
def find_strike_by_delta_yf(ticker, exp_date, price, target_delta):
    """
    Pick put strike closest to target_delta using yfinance option chain.
    Returns (strike, delta_approx, method) or (None, None, None).
    """
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if not opts:
            return None, None, None
        best_exp = min(opts, key=lambda e: abs((datetime.datetime.strptime(e, "%Y-%m-%d").date() - exp_date).days))
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return None, None, None
        candidates = puts[
            (puts["strike"] > price * 0.70) &
            (puts["strike"] < price * 0.97)
        ].copy()
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


# --- LIQUIDITY CHECK ---------------------------------------------------
def check_liquidity(ticker, exp_date, strike, max_ba=MAX_BID_ASK_WIDTH):
    """
    Returns (passes: bool, oi: int, spread: float, reason: str).
    OI >= 50 and B/A <= max_ba required.
    """
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


# --- OPTION PRICE ------------------------------------------------------
def get_option_price_yf(ticker, exp_date, strike):
    """Returns mid-price for the put, or None if unavailable. No fabricated fallback."""
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
            return None
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return None
        row = puts.iloc[(puts["strike"] - strike).abs().argsort()[:1]]
        if row.empty:
            return None
        bid = float(row["bid"].iloc[0])
        ask = float(row["ask"].iloc[0])
        lv  = float(row["lastPrice"].iloc[0])
        if bid > 0 and ask > 0 and ask > bid:
            return round((bid + ask) / 2, 2)
        if lv > 0:
            return round(lv, 2)
        return None
    except:
        return None

def get_best_expiry_yf(ticker):
    """Find expiry closest to TARGET_DTE within MIN_DTE to MAX_DTE range."""
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

# --- CORE SCANNER ------------------------------------------------------
def scan_ticker(ticker, vix, market_bullish=True):
    """
    Scans one ticker. Returns result dict with verdict.
    Verdicts: TAKE_IT | MANUAL_CHECK | SKIP
    TAKE_IT only when ALL required live/verified data passes.
    """
    r = {"ticker": ticker, "verdict": "SKIP", "reason": "", "data_quality": ""}
    cat = ticker_category(ticker)
    r["category"] = cat

    # Step 1 - Price & IV
    print("  Getting IV data...")
    d     = get_iv_data(ticker)
    price = d.get("price")
    if not price:
        r["reason"] = "No price data"
        return r
    iv      = d.get("iv", 0)
    hv      = d.get("hv", 0)
    ivr     = d.get("ivr", 0)
    ivr_src = d.get("ivr_source", "unknown")
    r.update({"price": price, "iv": iv, "hv": hv, "ivr": ivr, "ivr_source": ivr_src})

    # Step 2 - Market trend gate
    if not market_bullish:
        r["reason"] = "Market trend bearish (SPY+QQQ both below 50MA) - no new bull spreads"
        return r

    # Step 3 - Individual ticker trend
    trend_status, trend_detail = check_ticker_trend(ticker, price)
    r["trend"] = trend_status + " | " + trend_detail
    print("  Trend: " + trend_status + " | " + trend_detail)

    # Step 4 - Earnings gate
    safe_earn, days_e, date_e, earn_status = check_earnings(ticker)
    r["earnings"]        = date_e + " (" + str(days_e) + "d)"
    r["earnings_status"] = earn_status
    print("  Earnings: " + date_e + " (" + str(days_e) + "d) [" + earn_status + "]")

    if not safe_earn and earn_status == "CONFIRMED":
        r["reason"] = "Earnings in " + str(days_e) + "d - too close"
        return r

    # Step 5 - IVR gate
    effective_min_ivr = HR_MIN_IV_RANK if cat == "HIGH_RISK" else MIN_IV_RANK
    if ivr < effective_min_ivr:
        r["reason"] = "IVR " + str(ivr) + " < " + str(effective_min_ivr) + " (premium too cheap)"
        print("  SKIP: " + r["reason"])
        return r
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
        r["reason"] = "VIX > 30 - stand aside"
        return r

    # Step 6 - Get expiry & strikes
    effective_max_ba = HR_MAX_BID_ASK_WIDTH if cat == "HIGH_RISK" else MAX_BID_ASK_WIDTH
    av_opts    = av_get_options(ticker, tgt_delta)
    options_src = "AV"
    delta_used = None; delta_method = None
    oi = 0; ba_spread = 999
    short_mid = None; lm = None
    credit_verified = False

    if av_opts:
        exp_str     = av_opts["expiry"]
        dte         = av_opts["dte"]
        ss          = av_opts["short_strike"]
        ls          = av_opts["long_strike"]
        short_mid   = av_opts["short_mid"]
        delta_used  = av_opts["delta"]
        delta_method = av_opts["delta_method"]
        oi          = av_opts["oi"]
        ba_spread   = av_opts["ba_width"]
        exp_date    = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
        exp_disp    = exp_date.strftime("%b %d %Y")
        print("  [AV] " + exp_disp + " (" + str(dte) + "DTE) | $" + str(ss) + " delta:" + str(delta_used) + " | OI:" + str(oi) + " | B/A:$" + str(ba_spread))

        # Liquidity gates
        if oi < MIN_OPEN_INTEREST:
            r["reason"] = "Low open interest: OI " + str(oi) + " < " + str(MIN_OPEN_INTEREST)
            return r
        if ba_spread > effective_max_ba:
            r["reason"] = "Spread too wide: B/A $" + str(ba_spread) + " > $" + str(effective_max_ba)
            return r

        # Delta gate
        if delta_used > TARGET_DELTA_HIGH:
            r["reason"] = "Delta " + str(delta_used) + " > " + str(TARGET_DELTA_HIGH) + " - too close to ATM"
            return r

        # Credit: require BOTH legs verified
        lm = get_option_price_yf(ticker, exp_date, ls)
        if short_mid and short_mid > 0 and lm and lm >= 0 and short_mid > lm:
            credit = round(short_mid - lm, 2)
            credit_verified = True
        elif short_mid and short_mid > 0 and lm is None:
            # Long leg price missing - MANUAL_CHECK, never TAKE_IT
            r["verdict"] = "MANUAL_CHECK"
            r["reason"]  = "Could not verify long leg price - check live option chain manually"
            r["data_quality"] = "MISSING"
            r.update({"expiry": exp_disp, "dte": dte, "short_strike": ss, "long_strike": ls,
                      "delta": delta_used, "delta_method": delta_method, "options_src": options_src,
                      "open_interest": oi, "bid_ask": ba_spread})
            return r
        else:
            r["reason"] = "Could not verify credit - check broker manually"
            return r

    else:
        # yfinance fallback
        options_src = "yfinance"
        exp_str, dte = get_best_expiry_yf(ticker)
        if not exp_str:
            r["reason"] = "No expiry " + str(MIN_DTE) + "-" + str(MAX_DTE) + "DTE found"
            return r
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
            r["reason"] = "Liquidity fail: " + liq_reason
            print("  SKIP: " + r["reason"])
            return r
        print("  Liquidity OK | OI:" + str(oi) + " | B/A:$" + str(ba_spread))

        # Credit: require BOTH legs verified
        print("  Fetching option prices...")
        sm = get_option_price_yf(ticker, exp_date, ss)
        lm = get_option_price_yf(ticker, exp_date, ls)
        print("  Short put mid: $" + str(sm) + " | Long put mid: $" + str(lm))

        if sm is None:
            r["reason"] = "Could not verify short put price - check broker manually"
            return r
        if lm is None:
            r["verdict"]      = "MANUAL_CHECK"
            r["reason"]       = "Could not verify long leg price - check live option chain manually"
            r["data_quality"] = "MISSING"
            r.update({"expiry": exp_disp, "dte": dte, "short_strike": ss, "long_strike": ls,
                      "delta": delta_used, "delta_method": delta_method, "options_src": options_src,
                      "open_interest": oi, "bid_ask": ba_spread})
            return r
        if sm > lm:
            credit = round(sm - lm, 2)
            credit_verified = True
            short_mid = sm
        else:
            r["reason"] = "Invalid credit (short <= long price) - check broker manually"
            return r

    # Update result with option details
    r.update({
        "expiry"       : exp_disp,
        "dte"          : dte,
        "short_strike" : ss,
        "long_strike"  : ls,
        "delta"        : delta_used,
        "delta_method" : delta_method,
        "open_interest": oi,
        "bid_ask"      : ba_spread,
        "options_src"  : options_src,
    })

    r["credit"] = credit
    print("  Credit: $" + str(credit))

    # Credit minimum check
    min_credit = round(SPREAD_WIDTH * MIN_CREDIT_RATIO, 2)
    if credit < min_credit:
        r["reason"] = "Credit $" + str(credit) + " < minimum $" + str(min_credit) + " (1/3 of width)"
        print("  SKIP: " + r["reason"])
        return r

    # Step 7 - Risk metrics & sizing
    max_loss_per_contract = (SPREAD_WIDTH - credit) * 100
    base_contracts  = size_contracts(max_loss_per_contract, size_mod)
    contracts       = max(1, base_contracts)
    m = calc_metrics(credit, SPREAD_WIDTH, contracts)
    risk_pct = round(m["nl_usd"] / ACCOUNT_SIZE_USD * 100, 1)

    # Risk enforcement
    if risk_pct > 3.0:
        r["reason"] = "Risk " + str(risk_pct) + "% exceeds 3% limit - SKIP"
        print("  SKIP: " + r["reason"])
        return r

    if risk_pct <= 2:
        risk_warn = "OK: Within 2% rule (" + str(risk_pct) + "% of account)"
    elif risk_pct <= 3:
        risk_warn = "BORDERLINE: 2-3% risk (" + str(risk_pct) + "%) - reduce size"
    else:
        risk_warn = "EXCEEDS LIMIT: " + str(risk_pct) + "% - do not place"

    # Step 8 - Delta unknown check
    if delta_used is None:
        # delta is completely unknown - MANUAL_CHECK
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "Delta unknown - verify strike in broker before placing"
        r["data_quality"] = "ESTIMATED"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # Step 9 - IVR source check: estimated IVR -> MANUAL_CHECK
    if "estimated" in ivr_src.lower() and cat != "ETF":
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "IVR is estimated (not from Barchart) - confirm IVR before placing"
        r["data_quality"] = "ESTIMATED"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # Step 10 - Earnings unknown for single stocks -> MANUAL_CHECK
    if earn_status == "UNKNOWN" and cat != "ETF":
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "Earnings date unknown for " + ticker + " - verify no earnings within 14 days"
        r["data_quality"] = "MISSING"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # Step 11 - Borderline risk -> MANUAL_CHECK for >2% risk
    if risk_pct > 2.0:
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "Risk " + str(risk_pct) + "% exceeds 2% - reduce size before placing"
        r["data_quality"] = "VERIFIED"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # Step 12 - Trend degradation
    if trend_status == "BEARISH":
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "Ticker below 200MA - bearish trend, verify carefully"
        r["data_quality"] = "VERIFIED"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # Step 13 - High-risk stocks need strong conditions
    if cat == "HIGH_RISK" and (ivr < HR_MIN_IV_RANK or earn_status != "CONFIRMED"):
        r["verdict"]      = "MANUAL_CHECK"
        r["reason"]       = "High-risk stock: IVR < 50 or earnings not confirmed - verify all data"
        r["data_quality"] = "ESTIMATED"
        r.update({"contracts": contracts, "np": m["np_usd"], "np_rm": m["np_rm"],
                  "nl": m["nl_usd"], "nl_rm": m["nl_rm"], "fees": m["fees"], "pop": m["pop"],
                  "be": round(ss - credit, 2), "risk_pct": risk_pct, "risk_warn": risk_warn,
                  "size_note": "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x"})
        return r

    # ALL checks passed -> TAKE_IT
    r.update({
        "verdict"     : "TAKE_IT",
        "data_quality": "VERIFIED",
        "contracts"   : contracts,
        "np"          : m["np_usd"],
        "np_rm"       : m["np_rm"],
        "nl"          : m["nl_usd"],
        "nl_rm"       : m["nl_rm"],
        "fees"        : m["fees"],
        "pop"         : m["pop"],
        "be"          : round(ss - credit, 2),
        "risk_pct"    : risk_pct,
        "risk_warn"   : risk_warn,
        "size_note"   : "IVRx" + str(ivr_mod) + " VIXx" + str(vix_mod) + " = " + str(size_mod) + "x",
    })
    print("  TAKE_IT | Credit:$" + str(credit) + " | " + str(contracts) + "x | Loss:$" + str(m["nl_usd"]) + " | PoP:" + str(m["pop"]) + "% | " + risk_warn)
    return r

# --- MESSAGE FORMATTERS ------------------------------------------------
def fmt_market(vix, mkt, market_trend_reason):
    spy = mkt.get("SPY", {})
    qqq = mkt.get("QQQ", {})
    vix_warn = "\n  VIX elevated - position sizes reduced 50%" if vix and vix >= 25 else ""
    return (
        "BILLY SCANNER - " + str(datetime.date.today()) + "\n"
        + "================================\n"
        + "SPY: $" + str(spy.get("price","?")) + " (" + str(spy.get("pct",0)) + "%)\n"
        + "QQQ: $" + str(qqq.get("price","?")) + " (" + str(qqq.get("pct",0)) + "%)\n"
        + "VIX: " + str(vix) + " - " + vix_label(vix) + vix_warn + "\n"
        + "Trend: " + market_trend_reason + "\n"
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
    credit  = r.get("credit", 0)
    src_note = "Data: " + r.get("options_src","?") + " | IVR: " + r.get("ivr_source","?")

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
        "Expiry  : " + r.get("expiry","?") + " (" + str(r.get("dte","?")) + " DTE)",
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

def fmt_summary(results, vix, market_trend_ok):
    takes   = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    manuals = [r["ticker"] for r in results if r["verdict"] == "MANUAL_CHECK"]
    skips   = [r["ticker"] for r in results if r["verdict"] == "SKIP"]
    warn = "\n  VIX " + str(vix) + " elevated - reduce all sizes" if vix and vix > 25 else ""
    trend_warn = "\n  Market trend bearish - no TAKE_IT signals today" if not market_trend_ok else ""
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
        + "  IVR >= 30 confirmed in broker/Barchart\n"
        + "  Delta ~0.30 (max 0.35) via live broker Greeks\n"
        + "  OI >= " + str(MIN_OPEN_INTEREST) + " | B/A <= $" + str(MAX_BID_ASK_WIDTH) + "\n"
        + "  No earnings within " + str(EARNINGS_BUFFER) + " days\n"
        + "  Risk <= 2% of account ($" + str(MAX_RISK_USD) + ")\n"
        + "  This scanner is for screening only.\n"
        + "  Not financial advice."
    )

# --- MAIN --------------------------------------------------------------
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
    market_bullish, market_trend_reason = check_market_trend()
    print("Market trend: " + market_trend_reason)
    send_telegram(fmt_market(vix, mkt, market_trend_reason))

    # Portfolio exposure counters
    take_it_count         = 0
    high_risk_take_count  = 0

    results = []
    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print("\n[" + str(i) + "/" + str(len(WATCHLIST)) + "] " + ticker + " (AV: " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT) + ")")
            r = scan_ticker(ticker, vix, market_bullish)

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

    send_telegram(fmt_summary(results, vix, market_bullish))
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    print("\nDONE | Trades found: " + str(takes or "None") + " | AV: " + str(AV_CALL_COUNT) + "/" + str(AV_FREE_LIMIT))


if __name__ == "__main__":
    run()
