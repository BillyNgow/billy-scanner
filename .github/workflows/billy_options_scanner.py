#!/usr/bin/env python3
"""
Billy Options Scanner — Cloud Version (GitHub Actions)
Framework: Tom Sosnoff / tastytrade bull put spread
Knowledge:  tastytrade_bull_put_spread_knowledge.md

Data sources (priority order):
  1. Alpha Vantage — price (GLOBAL_QUOTE) + options (HISTORICAL_OPTIONS)
  2. yfinance      — HV, IVR calc, options chain fallback, VIX, earnings
  3. Barchart      — IVR scrape (most reliable IVR source)

No IBKR / TWS required. Runs headless on GitHub Actions.
Secrets (set in GitHub repo → Settings → Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, AV_API_KEY
"""

import os, re, datetime, time, math, warnings
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# Credentials — from GitHub Secrets (never hardcode)
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
AV_API_KEY       = os.environ.get("AV_API_KEY", "")

# Account — tastytrade_bull_put_spread_knowledge.md → Scanner Alignment Reference
ACCOUNT_SIZE_USD = 25000           # $25,000 USD
MAX_RISK_PCT     = 0.02            # 2% max risk per trade
MAX_RISK_USD     = ACCOUNT_SIZE_USD * MAX_RISK_PCT   # $500 USD

USD_MYR_RATE     = 4.40

# Watchlist — knowledge file preferred underlyings (in order of liquidity)
# COIN, MSTR kept with caution note per knowledge file
WATCHLIST = [
    "SPY","QQQ","IWM","GLD","TLT","XLE","XLF",
    "AAPL","NVDA","TSLA","AMD","META","AMZN","PLTR",
    "COIN","MSTR"
]

ETF_LIST = [
    "SPY","QQQ","IWM","DIA","GLD","TLT","USO","SLV",
    "EEM","XLE","XLF","FXI","ARKK","SOXX"
]

# Entry rules — knowledge file: Core Entry Rules
MIN_IV_RANK      = 30              # IVR ≥ 30 minimum; ≥ 50 ideal
MIN_DTE          = 25              # 45 DTE ±7 days; min 25
MAX_DTE          = 52              # 45 DTE ±7 days
TARGET_DTE       = 45              # sweet spot
SPREAD_WIDTH     = 5               # 5-point spread
MIN_CREDIT_RATIO = 0.33            # credit ≥ 1/3 of spread width
EARNINGS_BUFFER  = 14              # skip if earnings within 14 days
IBKR_FEE         = 0.79           # per contract per leg

# Delta — knowledge file: Delta Selection Tiers
TARGET_DELTA_LOW  = 0.20           # conservative (IVR 30–50)
TARGET_DELTA_HIGH = 0.35           # absolute max — never exceed
TARGET_DELTA      = 0.30           # default target

# Liquidity — knowledge file: Liquidity Rules
MIN_OPEN_INTEREST = 50             # OI ≥ 50 on short strike
MAX_BID_ASK_WIDTH = 0.50           # B/A ≤ $0.50 on short put

# Alpha Vantage
AV_BASE       = "https://www.alphavantage.co/query"
AV_CALL_COUNT = 0
AV_FREE_LIMIT = 25                 # free tier: 25 req/day


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [Telegram not configured]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print("  Telegram OK" if r.status_code == 200 else f"  Telegram err:{r.status_code}")
    except Exception as e:
        print(f"  Telegram error: {e}")


# ─── FEE & METRIC HELPERS ─────────────────────────────────────────────────────
def calc_fees(contracts=1):
    # open + close × 2 legs × contracts
    return round(IBKR_FEE * 2 * 2 * contracts, 2)

def calc_metrics(credit, width, contracts=1):
    gross_profit = credit * 100 * contracts
    gross_loss   = (width - credit) * 100 * contracts
    fees         = calc_fees(contracts)
    # POP ≈ 1 - (credit / width) — tastytrade approximation
    pop          = round((1 - credit / width) * 100, 1) if width > 0 else 0
    return {
        "np_usd": round(gross_profit - fees, 2),
        "nl_usd": round(gross_loss + fees, 2),
        "np_rm":  round((gross_profit - fees) * USD_MYR_RATE, 2),
        "nl_rm":  round((gross_loss + fees) * USD_MYR_RATE, 2),
        "fees":   round(fees, 2),
        "pop":    pop,
    }

def size_contracts(max_loss_per_contract):
    """
    knowledge file position sizing formula:
      Max contracts = Floor(MAX_RISK_USD / max_loss_per_spread)
    """
    if max_loss_per_contract <= 0:
        return 1
    return max(1, math.floor(MAX_RISK_USD / max_loss_per_contract))


# ─── MARKET DATA ──────────────────────────────────────────────────────────────
def get_vix():
    # AV doesn't carry ^VIX — yfinance only
    try:
        h = yf.Ticker("^VIX").history(period="5d")
        return round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
    except:
        return None

def get_market():
    out = {}
    for t in ["SPY", "QQQ"]:
        pdata = av_get_price(t)
        if pdata and pdata.get("prev"):
            price = pdata["price"]; prev = pdata["prev"]
            pct   = round((price - prev) / prev * 100, 2)
            out[t] = {"price": price, "pct": pct}
            print(f"  AV: {t} ${price} ({pct:+.1f}%)")
        else:
            try:
                h = yf.Ticker(t).history(period="5d")
                if len(h) >= 2:
                    p  = round(float(h["Close"].iloc[-1]), 2)
                    pc = round((p - float(h["Close"].iloc[-2])) / float(h["Close"].iloc[-2]) * 100, 2)
                    out[t] = {"price": p, "pct": pc}
                    print(f"  yf: {t} ${p} ({pc:+.1f}%)")
            except:
                pass
    return out

def vix_label(v):
    if v is None: return "Unknown"
    if v < 15:   return "Low Fear"
    if v < 20:   return "Neutral"
    if v < 25:   return "Slightly Elevated — trade smaller"
    if v < 30:   return "Elevated — reduce size"
    return "HIGH FEAR — stand aside"

def vix_size_modifier(v):
    """
    knowledge file VIX rules:
      VIX 25–30 → reduce position size 50%
      VIX > 30  → scanner halts (handled in run())
    """
    if v is None: return 1.0
    if v >= 30:   return 0.0
    if v >= 25:   return 0.5
    return 1.0


# ─── EARNINGS CHECK ───────────────────────────────────────────────────────────
def check_earnings(ticker):
    if ticker in ETF_LIST:
        return True, 999, "ETF — no earnings"
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date", [])
            if ed:
                dt   = pd.Timestamp(ed[0]).date()
                days = (dt - datetime.date.today()).days
                return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")
    except:
        pass
    return True, 999, "Unknown"


# ─── ALPHA VANTAGE HELPERS ────────────────────────────────────────────────────
def _av_get(params):
    """Single Alpha Vantage API call with quota guard. Returns JSON or None."""
    global AV_CALL_COUNT
    if AV_CALL_COUNT >= AV_FREE_LIMIT:
        print(f"  [AV quota {AV_CALL_COUNT}/{AV_FREE_LIMIT} reached]")
        return None
    if not AV_API_KEY:
        return None
    try:
        params["apikey"] = AV_API_KEY
        r = requests.get(AV_BASE, params=params, timeout=15)
        AV_CALL_COUNT += 1
        if r.status_code != 200:
            print(f"  AV HTTP {r.status_code}")
            return None
        data = r.json()
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information", "")
            print(f"  AV rate-limit: {msg[:80]}")
            AV_CALL_COUNT = AV_FREE_LIMIT
            return None
        return data
    except Exception as e:
        print(f"  AV error: {e}")
        return None

def av_get_price(ticker):
    """AV GLOBAL_QUOTE → price + previous close."""
    data = _av_get({"function": "GLOBAL_QUOTE", "symbol": ticker})
    if not data:
        return None
    q     = data.get("Global Quote", {})
    price = q.get("05. price")
    prev  = q.get("08. previous close")
    if not price:
        return None
    return {
        "price": round(float(price), 2),
        "prev":  round(float(prev), 2) if prev else None
    }

def av_get_options(ticker, target_delta=TARGET_DELTA):
    """
    AV HISTORICAL_OPTIONS → put closest to target_delta at ~45 DTE.
    knowledge file: delta is the primary strike selector.
    ETFs skipped to conserve quota (yfinance handles them fine).
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
    puts  = [
        c for c in raw
        if c.get("type", "").lower() == "put"
        and c.get("expiration")
        and MIN_DTE <= (datetime.datetime.strptime(c["expiration"], "%Y-%m-%d").date() - today).days <= MAX_DTE
        and float(c.get("implied_volatility") or 0) > 0.01
    ]
    if not puts:
        return None

    # Expiry closest to TARGET_DTE (45 DTE)
    def dte_dist(c):
        return abs((datetime.datetime.strptime(c["expiration"], "%Y-%m-%d").date() - today).days - TARGET_DTE)
    puts.sort(key=dte_dist)
    target_exp  = puts[0]["expiration"]
    exp_date    = datetime.datetime.strptime(target_exp, "%Y-%m-%d").date()
    days_to_exp = (exp_date - today).days
    exp_puts    = [c for c in puts if c["expiration"] == target_exp]

    # knowledge file: pick strike whose delta is closest to target_delta
    # put delta is negative — compare absolute value
    def delta_dist(c):
        return abs(abs(float(c.get("delta") or 0)) - target_delta)
    exp_puts.sort(key=delta_dist)
    chosen = exp_puts[0]

    short_strike = float(chosen["strike"])
    long_strike  = round(short_strike - SPREAD_WIDTH, 2)
    iv_raw       = float(chosen.get("implied_volatility", 0))
    iv_pct       = round(iv_raw * 100 if iv_raw < 3 else iv_raw, 1)
    delta_val    = round(abs(float(chosen.get("delta") or 0)), 3)

    bid      = float(chosen.get("bid") or 0)
    ask      = float(chosen.get("ask") or 0)
    lp       = float(chosen.get("last") or 0)
    oi       = int(float(chosen.get("open_interest") or 0))
    ba_width = round(ask - bid, 2) if ask > bid else 999
    short_mid = round((bid + ask) / 2, 2) if bid > 0 and ask > bid else round(lp, 2)

    return {
        "iv":           iv_pct,
        "expiry":       target_exp,
        "dte":          days_to_exp,
        "short_strike": short_strike,
        "long_strike":  long_strike,
        "short_mid":    short_mid,
        "delta":        delta_val,
        "oi":           oi,
        "ba_width":     ba_width,
        "delta_method": "AV Greeks",
        "source":       "AlphaVantage"
    }


# ─── IV RANK SOURCES ──────────────────────────────────────────────────────────
def get_ivr_barchart(ticker):
    """Scrape IVR from Barchart — most reliable free IVR source."""
    try:
        url = f"https://www.barchart.com/stocks/quotes/{ticker}/overview"
        h   = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"}
        r   = requests.get(url, headers=h, timeout=12)
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
            "ivr": max(0, min(100, ivr)), "samples": len(iv_list)
        }
    except:
        return {}

def get_iv_data(ticker):
    """
    Unified IV data.
    Price  → AV GLOBAL_QUOTE > yfinance
    IVR    → Barchart scrape > yfinance calc
    IV/HV  → yfinance (no AV quota cost)
    """
    av_price  = av_get_price(ticker)
    price     = av_price["price"] if av_price else None
    price_src = "AV" if av_price else "yf"
    yfd       = get_iv_yfinance(ticker)
    if price is None:
        price = yfd.get("price")
    iv  = yfd.get("iv", 0)
    hv  = yfd.get("hv", 0)
    bvr = get_ivr_barchart(ticker)
    ivr = bvr if bvr is not None else yfd.get("ivr", 0)
    src = "Barchart" if bvr is not None else "yfinance"
    print(f"  [{price_src}] ${price} | IV:{iv}% | HV:{hv}% | IVR:{ivr} [{src}]")
    return {"price": price, "iv": iv, "hv": hv, "ivr": ivr, "source": src}


# ─── DELTA-BASED STRIKE SELECTION ─────────────────────────────────────────────
def find_strike_by_delta_yf(ticker, exp_date, price, target_delta):
    """
    knowledge file strike selection method 2: IV-approximation via yfinance.
    Picks put strike closest to target_delta using the options chain.
    Returns (strike, delta_approx, method).
    """
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if not opts:
            return None, None, None

        best_exp = min(
            opts,
            key=lambda e: abs(
                (datetime.datetime.strptime(e, "%Y-%m-%d").date() - exp_date).days
            )
        )
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return None, None, None

        # Filter to realistic OTM range for a short put
        candidates = puts[
            (puts["strike"] > price * 0.70) &
            (puts["strike"] < price * 0.97)
        ].copy()
        if candidates.empty:
            return None, None, None

        # Use ~12–15% OTM as 0.25–0.30 delta proxy per knowledge file method 3
        otm_target = price * (1 - target_delta * 0.40)
        row = candidates.iloc[(candidates["strike"] - otm_target).abs().argsort()[:1]]
        if row.empty:
            return None, None, None

        strike = float(row["strike"].iloc[0])
        # Delta approximation from IV if available
        iv_val = float(row["impliedVolatility"].iloc[0]) if "impliedVolatility" in row.columns else 0
        if iv_val > 0:
            delta_approx = round(min(0.35, max(0.15, 0.5 - (price - strike) / (price * iv_val * (TARGET_DTE / 365) ** 0.5 + 1e-9))), 3)
        else:
            delta_approx = 0.28   # fixed proxy — flag in output

        return strike, delta_approx, "IV-approx"
    except Exception as e:
        print(f"  IV-approx error: {e}")
        return None, None, None


# ─── LIQUIDITY CHECK ──────────────────────────────────────────────────────────
def check_liquidity(ticker, exp_date, strike):
    """
    knowledge file liquidity rules:
      OI ≥ 50 on short strike → else SKIP "Low open interest"
      B/A ≤ $0.50 on short put → else SKIP "Spread too wide"
    Returns (passes: bool, oi: int, spread: float, reason: str).
    """
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if not opts:
            return False, 0, 0, "No option chain"

        best_exp = min(
            opts,
            key=lambda e: abs(
                (datetime.datetime.strptime(e, "%Y-%m-%d").date() - exp_date).days
            )
        )
        puts = tk.option_chain(best_exp).puts
        if puts.empty:
            return False, 0, 0, "Empty puts chain"

        row = puts.iloc[(puts["strike"] - strike).abs().argsort()[:1]]
        if row.empty:
            return False, 0, 0, "Strike not found"

        bid = float(row["bid"].iloc[0])
        ask = float(row["ask"].iloc[0])
        oi  = int(row["openInterest"].iloc[0]) if "openInterest" in row.columns else 0
        spread = round(ask - bid, 2) if ask > bid else 0

        if oi < MIN_OPEN_INTEREST:
            return False, oi, spread, f"OI {oi} < {MIN_OPEN_INTEREST} (low open interest)"
        if spread > MAX_BID_ASK_WIDTH:
            return False, oi, spread, f"B/A ${spread:.2f} > ${MAX_BID_ASK_WIDTH} (spread too wide)"

        return True, oi, spread, "OK"
    except Exception as e:
        return False, 0, 0, f"Liquidity check error: {e}"


# ─── OPTION PRICE ─────────────────────────────────────────────────────────────
def get_option_price_yf(ticker, exp_date, strike):
    """
    Returns mid-price for the put, or None if unavailable.
    No fabricated fallback — returns None and caller decides.
    """
    try:
        tk        = yf.Ticker(ticker)
        opts      = tk.options
        best_exp  = None
        best_diff = 999
        for exp in opts:
            try:
                ed   = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                diff = abs((ed - exp_date).days)
                if diff < best_diff:
                    best_diff = diff
                    best_exp  = exp
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
    """Find expiry closest to TARGET_DTE within MIN_DTE–MAX_DTE range."""
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


# ─── CORE SCANNER ─────────────────────────────────────────────────────────────
def scan_ticker(ticker, vix):
    r = {"ticker": ticker, "verdict": "SKIP", "reason": ""}

    # Step 1 — Price & IV
    print("  Getting IV data...")
    d     = get_iv_data(ticker)
    price = d.get("price")
    if not price:
        r["reason"] = "No price data"
        return r
    iv  = d.get("iv", 0)
    hv  = d.get("hv", 0)
    ivr = d.get("ivr", 0)
    r.update({"price": price, "iv": iv, "hv": hv, "ivr": ivr})

    # Step 2 — Earnings gate
    safe, days_e, date_e = check_earnings(ticker)
    r["earnings"] = f"{date_e} ({days_e}d)"
    print(f"  Earnings: {date_e} ({days_e}d)")
    if not safe:
        r["reason"] = f"Earnings in {days_e}d — too close"
        return r

    # Step 3 — IVR gate
    # knowledge file: < 30 = hard pass; 30–50 = reduce size; ≥ 50 = full size
    if ivr < MIN_IV_RANK:
        r["reason"] = f"IVR {ivr:.0f} < {MIN_IV_RANK} (premium too cheap)"
        print(f"  SKIP: {r['reason']}")
        return r
    print(f"  IVR {ivr:.0f} passes gate")

    # IVR tier → target delta + size modifier
    if ivr >= 50:
        ivr_label  = "Strong (≥50) — full size"
        ivr_mod    = 1.0
        tgt_delta  = 0.30   # knowledge file: 0.25–0.30 Δ, up to 0.35 when IVR ≥ 50
    else:
        ivr_label  = "Acceptable (30–50) — reduce size"
        ivr_mod    = 0.5    # knowledge file: reduce size or widen strikes
        tgt_delta  = 0.25   # knowledge file: 0.20–0.25 Δ when IVR 30–50

    r["ivr_label"] = ivr_label

    # VIX modifier — knowledge file: VIX 25–30 reduce 50%
    vix_mod  = vix_size_modifier(vix or 0)
    size_mod = ivr_mod * vix_mod
    if size_mod == 0:
        r["reason"] = "VIX > 30 — stand aside"
        return r

    # Step 4 — Get expiry & strikes
    # Try AV options first (non-ETFs only to save quota)
    av_opts     = av_get_options(ticker, tgt_delta)
    options_src = "AV"

    if av_opts:
        exp_str      = av_opts["expiry"]
        dte          = av_opts["dte"]
        ss           = av_opts["short_strike"]
        ls           = av_opts["long_strike"]
        short_mid    = av_opts["short_mid"]
        delta_used   = av_opts["delta"]
        delta_method = av_opts["delta_method"]
        oi           = av_opts["oi"]
        ba_spread    = av_opts["ba_width"]
        exp_date     = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
        exp_disp     = exp_date.strftime("%b %d %Y")
        print(f"  [AV] {exp_disp} ({dte}DTE) | ${ss} Δ{delta_used:.3f} | OI:{oi} | B/A:${ba_spread}")

        # Liquidity gates — knowledge file auto-skip rules
        if oi < MIN_OPEN_INTEREST:
            r["reason"] = f"Low open interest: OI {oi} < {MIN_OPEN_INTEREST}"
            return r
        if ba_spread > MAX_BID_ASK_WIDTH:
            r["reason"] = f"Spread too wide: B/A ${ba_spread:.2f} > ${MAX_BID_ASK_WIDTH}"
            return r

        # Delta gate — knowledge file: max 0.35 Δ, never exceed
        if delta_used > TARGET_DELTA_HIGH:
            r["reason"] = f"Delta {delta_used:.3f} > {TARGET_DELTA_HIGH} — too close to ATM"
            return r

        # Credit — long leg via yfinance
        lm = get_option_price_yf(ticker, exp_date, ls)
        if short_mid and lm and short_mid > lm:
            credit = round(short_mid - lm, 2)
        elif short_mid and short_mid > 0:
            credit = round(short_mid * 0.45, 2)
            r["credit_note"] = "Long put price unavailable — credit estimated at 45% of short put"
        else:
            r["reason"] = "Could not verify credit — check IBKR manually"
            return r

    else:
        # yfinance fallback path
        options_src  = "yfinance"
        exp_str, dte = get_best_expiry_yf(ticker)
        if not exp_str:
            r["reason"] = f"No expiry {MIN_DTE}–{MAX_DTE}DTE found"
            return r
        exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
        exp_disp = exp_date.strftime("%b %d %Y")

        # Step 5 — Delta-based strike selection
        # knowledge file priority: (1) IBKR Greeks — N/A cloud
        #                          (2) IV-approximation via yfinance
        #                          (3) Fixed OTM proxy as last resort
        print("  Finding delta-based short strike (~0.30Δ)...")
        ss, delta_used, delta_method = find_strike_by_delta_yf(ticker, exp_date, price, tgt_delta)

        if ss is None:
            # Method 3: fixed OTM proxy — knowledge file says flag clearly
            ss           = round(price * 0.88 / 2.5) * 2.5
            delta_used   = None
            delta_method = "Fixed-OTM (12%) — verify delta in IBKR"

        ls = ss - SPREAD_WIDTH
        print(f"  [{options_src}] {exp_disp} ({dte}DTE) | ${ss} via {delta_method} | Long: ${ls}")

        # Step 6 — Liquidity check
        print("  Checking liquidity...")
        liq_ok, oi, ba_spread, liq_reason = check_liquidity(ticker, exp_date, ss)
        if not liq_ok:
            r["reason"] = f"Liquidity fail: {liq_reason}"
            print(f"  SKIP: {r['reason']}")
            return r
        print(f"  Liquidity OK | OI:{oi} | B/A:${ba_spread}")

        # Step 7 — Credit (no fabricated fallback)
        print("  Fetching option prices...")
        sm = get_option_price_yf(ticker, exp_date, ss)
        lm = get_option_price_yf(ticker, exp_date, ls)
        print(f"  Short put mid: ${sm} | Long put mid: ${lm}")

        if sm is None:
            r["reason"] = "Could not verify short put price — check IBKR manually"
            return r
        if lm is not None and sm > lm:
            credit = round(sm - lm, 2)
        elif sm > 0:
            credit = round(sm * 0.45, 2)
            r["credit_note"] = "Long put price unavailable — credit estimated at 45% of short put"
        else:
            r["reason"] = "Could not verify credit — check IBKR manually"
            return r

    r.update({
        "expiry": exp_disp, "dte": dte,
        "short_strike": ss, "long_strike": ls,
        "delta": delta_used, "delta_method": delta_method,
        "open_interest": oi, "bid_ask": ba_spread,
        "options_src": options_src,
    })

    r["credit"] = credit
    print(f"  Credit: ${credit:.2f}")

    # Credit minimum — knowledge file: ≥ 1/3 of spread width
    min_credit = round(SPREAD_WIDTH * MIN_CREDIT_RATIO, 2)
    if credit < min_credit:
        r["reason"] = f"Credit ${credit:.2f} < minimum ${min_credit:.2f} (1/3 of width)"
        print(f"  SKIP: {r['reason']}")
        return r

    # Step 8 — Risk metrics & sizing
    max_loss_per_contract = (SPREAD_WIDTH - credit) * 100
    base_contracts        = size_contracts(max_loss_per_contract)
    contracts             = max(1, math.floor(base_contracts * size_mod))
    m                     = calc_metrics(credit, SPREAD_WIDTH, contracts)
    risk_pct              = round(m["nl_usd"] / ACCOUNT_SIZE_USD * 100, 1)

    # knowledge file risk warning levels: ≤2% ✅ | 2–3% ⚠️ | >3% ❌
    if risk_pct <= 2:   risk_warn = f"✅ Within 2% rule ({risk_pct}% of account)"
    elif risk_pct <= 3: risk_warn = f"⚠️ Borderline 2–3% — reduce size ({risk_pct}%)"
    else:               risk_warn = f"❌ Exceeds 3% — do not place ({risk_pct}%)"

    r.update({
        "verdict":   "TAKE_IT",
        "contracts": contracts,
        "np":        m["np_usd"],
        "np_rm":     m["np_rm"],
        "nl":        m["nl_usd"],
        "nl_rm":     m["nl_rm"],
        "fees":      m["fees"],
        "pop":       m["pop"],
        "be":        round(ss - credit, 2),
        "risk_pct":  risk_pct,
        "risk_warn": risk_warn,
        "size_note": f"IVR×{ivr_mod:.1f} VIX×{vix_mod:.1f} = {size_mod:.1f}× size",
    })
    print(f"  ✅ TAKE IT | Credit:${credit:.2f} | {contracts}x | Loss:${m['nl_usd']} | PoP:{m['pop']}% | {risk_warn}")
    return r


# ─── MESSAGE FORMATTERS ───────────────────────────────────────────────────────
def fmt_market(vix, mkt):
    spy = mkt.get("SPY", {})
    qqq = mkt.get("QQQ", {})
    vix_warn = "\n⚠️ VIX elevated — position sizes reduced 50%" if vix and vix >= 25 else ""
    return (
        f"BILLY SCANNER — {datetime.date.today()}\n"
        f"================================\n"
        f"SPY: ${spy.get('price','?')} ({spy.get('pct',0):+.1f}%)\n"
        f"QQQ: ${qqq.get('price','?')} ({qqq.get('pct',0):+.1f}%)\n"
        f"VIX: {vix} — {vix_label(vix)}{vix_warn}\n"
        f"Account: ${ACCOUNT_SIZE_USD:,} USD | Max risk/trade: ${MAX_RISK_USD:.0f}\n"
        f"================================"
    )

def fmt_trade(r):
    delta_str = f"{r['delta']:.3f}" if r.get("delta") else "~0.28 (approx)"
    note      = f"\nNote: {r['credit_note']}" if r.get("credit_note") else ""
    src_note  = f"Data: {r.get('options_src','?')} | Verify live in IBKR"
    return (
        f"✅ TRADE: {r['ticker']} — TAKE IT\n"
        f"================================\n"
        f"SELL: ${r['short_strike']} Put (Δ {delta_str})\n"
        f"BUY:  ${r['long_strike']} Put\n"
        f"Expiry: {r['expiry']} ({r['dte']} DTE)\n"
        f"IVR: {r.get('ivr',0):.0f} — {r.get('ivr_label','')}\n"
        f"IV: {r.get('iv','?')}% | OI: {r.get('open_interest','?')} | B/A: ${r.get('bid_ask','?')}\n"
        f"\nECONOMICS\n"
        f"Credit:  ${r['credit']:.2f}{note}\n"
        f"Profit:  ${r['np']:.2f} / RM{r['np_rm']:.0f}\n"
        f"Loss:    ${r['nl']:.2f} / RM{r['nl_rm']:.0f}\n"
        f"B/Even:  ${r['be']:.2f} | PoP: {r['pop']}%\n"
        f"Fees:    ${r['fees']:.2f}\n"
        f"\n{r['contracts']} contract(s) | {r['risk_warn']}\n"
        f"({r.get('size_note','')})\n"
        f"Earnings: {r.get('earnings','?')}\n"
        f"\nMANAGEMENT\n"
        f"Take profit: ${r['credit']/2:.2f} debit (50% of credit)\n"
        f"Stop loss:   ${r['credit']*2:.2f} debit (2× credit)\n"
        f"Close by:    21 DTE (gamma risk)\n"
        f"Exit if:     price breaks below ${r['short_strike']}\n"
        f"\n{src_note}"
    )

def fmt_skip(r):
    return (
        f"⏭ SKIP: {r['ticker']}\n"
        f"Reason: {r['reason']}\n"
        f"IVR: {r.get('ivr',0):.0f} | Earnings: {r.get('earnings','?')}"
    )

def fmt_summary(results, vix):
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    skips = [r["ticker"] for r in results if r["verdict"] == "SKIP"]
    warn  = f"\n⚠️ VIX {vix} elevated — reduce all sizes" if vix and vix > 25 else ""
    return (
        f"SCAN SUMMARY\n"
        f"================================\n"
        f"Scanned: {len(results)}/{len(WATCHLIST)}\n"
        f"Trades:  {', '.join(takes) if takes else 'None today'}\n"
        f"Skipped: {len(skips)}{warn}\n"
        f"AV calls: {AV_CALL_COUNT}/{AV_FREE_LIMIT}\n"
        f"================================\n"
        f"Always verify before placing:\n"
        f"• IVR ≥ 30 confirmed in IBKR/Barchart\n"
        f"• Delta ~0.30 Δ (max 0.35 Δ) via IBKR live Greeks\n"
        f"• OI ≥ {MIN_OPEN_INTEREST} | B/A ≤ ${MAX_BID_ASK_WIDTH}\n"
        f"• No earnings within {EARNINGS_BUFFER} days\n"
        f"• Risk ≤ 2% of account (${MAX_RISK_USD:.0f})"
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    now = datetime.datetime.utcnow()
    print("=" * 55)
    print("BILLY OPTIONS SCANNER — tastytrade framework")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC = 9:30 PM MYT")
    print(f"Account: ${ACCOUNT_SIZE_USD:,} USD | Max risk/trade: ${MAX_RISK_USD:.0f} (2%)")
    print(f"AV key:  {'configured' if AV_API_KEY else 'MISSING — set AV_API_KEY secret'}")
    print(f"Watchlist: {len(WATCHLIST)} tickers")
    print("=" * 55)

    send_telegram(
        f"Billy Scanner Starting\n"
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC (9:30 PM MYT)\n"
        f"Account: ${ACCOUNT_SIZE_USD:,} USD | Risk limit: ${MAX_RISK_USD:.0f}/trade\n"
        f"Scanning {len(WATCHLIST)} tickers: {', '.join(WATCHLIST)}"
    )

    vix = get_vix()
    mkt = get_market()
    print(f"VIX: {vix} — {vix_label(vix)}")
    send_telegram(fmt_market(vix, mkt))

    # knowledge file: VIX > 30 → scanner halts
    if vix and vix > 30:
        msg = (
            f"🚨 VIX ALERT: {vix}\n"
            f"VIX > 30 = High Fear\n"
            f"Scanner halted — stand aside today.\n"
            f"Gap risk overwhelms statistical edge."
        )
        print(msg)
        send_telegram(msg)
        return

    results = []
    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print(f"\n[{i}/{len(WATCHLIST)}] {ticker}  (AV: {AV_CALL_COUNT}/{AV_FREE_LIMIT})")
            r = scan_ticker(ticker, vix)
            results.append(r)
            send_telegram(fmt_trade(r) if r["verdict"] == "TAKE_IT" else fmt_skip(r))
            time.sleep(2)
        except Exception as e:
            print(f"  Error scanning {ticker}: {e}")
            continue

    send_telegram(fmt_summary(results, vix))
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    print(f"\nDONE | Trades found: {takes or 'None'} | AV: {AV_CALL_COUNT}/{AV_FREE_LIMIT}")


if __name__ == "__main__":
    run()
