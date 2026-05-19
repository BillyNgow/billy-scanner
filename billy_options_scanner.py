#!/usr/bin/env python3
"""
Billy Options Scanner - Cloud Version v3
Runs on GitHub Actions | Sends Telegram alerts
"""

import os, re, datetime, time, warnings, signal
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd

# ── CONFIG ────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

ACCOUNT_SIZE_USD = 10000
MAX_RISK_USD     = ACCOUNT_SIZE_USD * 0.02   # Hard 2% = $200 max loss per trade
USD_MYR_RATE     = 4.40
WATCHLIST        = ["TSLA","PLTR","AMD","MU","NVDA","META","NFLX","AAPL","AMZN","GOOGL","SPY","QQQ","IWM"]
ETF_LIST         = {"SPY","QQQ","IWM","DIA","GLD","TLT"}
MIN_IV_RANK      = 30
MIN_DTE          = 25
MAX_DTE          = 45
SPREAD_WIDTH     = 5
MIN_CREDIT_RATIO = 0.30        # FIX: was 0.33 but fallback credit is 0.30 → always skipped
# FIX: earnings buffer must cover the full trade window, not just 14d
EARNINGS_BUFFER  = MAX_DTE     # Skip if earnings within 45 days of today
IBKR_FEE         = 0.65        # FIX: IBKR standard rate is $0.65/contract, not $0.79

# ── YAHOO FINANCE — Direct API with crumb (bypasses yfinance where possible) ──
YF_SESSION  = None
YF_CRUMB    = None

def _build_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })
    return s

def get_yf_session():
    """Get a cookie-primed session. Called once at startup."""
    global YF_SESSION, YF_CRUMB
    if YF_SESSION:
        return YF_SESSION
    YF_SESSION = _build_session()
    # Try to get a valid cookie + crumb (required by Yahoo Finance since 2024)
    for url in ["https://fc.yahoo.com", "https://finance.yahoo.com"]:
        try:
            YF_SESSION.get(url, timeout=8)
        except Exception:
            pass
    try:
        r = YF_SESSION.get("https://query1.finance.yahoo.com/v1/test/csrfToken", timeout=8)
        if r.status_code == 200:
            YF_CRUMB = r.text.strip()
            print(f"  Yahoo crumb: {YF_CRUMB[:10]}...")
    except Exception:
        pass
    return YF_SESSION

def yf_ticker(symbol):
    """yfinance Ticker with our session injected."""
    s = get_yf_session()
    try:
        return yf.Ticker(symbol, session=s)
    except TypeError:
        return yf.Ticker(symbol)

def yf_history_direct(symbol, period_days=60):
    """
    Fetch OHLCV directly from Yahoo Finance v8 API.
    Falls back to yfinance if direct call fails.
    """
    s = get_yf_session()
    end   = int(time.time())
    start = end - period_days * 86400
    params = {"interval": "1d", "period1": start, "period2": end}
    if YF_CRUMB:
        params["crumb"] = YF_CRUMB
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = s.get(url, params=params, timeout=12)
        if r.status_code == 200:
            d    = r.json()
            res  = d["chart"]["result"][0]
            ts   = res["timestamp"]
            q    = res["indicators"]["quote"][0]
            closes = q["close"]
            # Filter out None values
            valid = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if len(valid) >= 5:
                df = pd.DataFrame(valid, columns=["ts", "Close"])
                df["Date"] = pd.to_datetime(df["ts"], unit="s")
                df = df.set_index("Date")[["Close"]]
                return df
    except Exception as e:
        pass
    # Fallback: yfinance
    try:
        tk = yf_ticker(symbol)
        h  = tk.history(period=f"{period_days}d")
        if not h.empty:
            return h[["Close"]]
    except Exception:
        pass
    return pd.DataFrame()

# ── TELEGRAM ──────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [Telegram off] {msg[:60]}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
            if r.status_code == 200:
                print("  Telegram OK")
                return
            print(f"  Telegram {r.status_code}")
        except Exception as e:
            print(f"  Telegram error (attempt {attempt+1}): {e}")
            time.sleep(2)

# ── FEES ──────────────────────────────────────────────────
# 2 legs × open+close (2 fills each side) × $0.65/contract
def calc_fees(contracts=1):
    return round(IBKR_FEE * 2 * 2 * contracts, 2)

def calc_metrics(credit, width, contracts=1):
    fees  = calc_fees(contracts)
    gp    = credit * 100 * contracts
    gl    = (width - credit) * 100 * contracts
    pop   = round((1 - credit / width) * 100, 1) if width > 0 else 0
    return {
        "np_usd": round(gp - fees, 2),
        "nl_usd": round(gl + fees, 2),
        "np_rm":  round((gp - fees) * USD_MYR_RATE, 2),
        "nl_rm":  round((gl + fees) * USD_MYR_RATE, 2),
        "fees":   fees,
        "pop":    pop,
    }

# ── MARKET ────────────────────────────────────────────────
def get_vix():
    try:
        df = yf_history_direct("^VIX", period_days=5)
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return None

def get_market():
    out = {}
    for t in ["SPY", "QQQ"]:
        try:
            df = yf_history_direct(t, period_days=5)
            if len(df) >= 2:
                p  = round(float(df["Close"].iloc[-1]), 2)
                pc = round((p - float(df["Close"].iloc[-2])) / float(df["Close"].iloc[-2]) * 100, 2)
                out[t] = {"price": p, "pct": pc}
        except Exception:
            pass
    return out

def vix_label(v):
    if v is None: return "Unknown (data error)"
    if v < 15:    return "Low Fear"
    if v < 20:    return "Neutral"
    if v < 30:    return "Elevated"
    return "HIGH FEAR - stand aside"

# ── EARNINGS ──────────────────────────────────────────────
def check_earnings(ticker):
    """
    FIX: Buffer is now MAX_DTE (45d) not 14d.
    A 25-45 DTE trade entered today with earnings in 15d = earnings inside trade!
    """
    if ticker in ETF_LIST:
        return True, 999, "ETF-no earnings"
    try:
        cal = yf_ticker(ticker).calendar
        if cal is None:
            return True, 999, "Unknown"
        # New yfinance returns dict
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date", [])
            if ed:
                dt   = pd.Timestamp(ed[0]).date()
                days = (dt - datetime.date.today()).days
                return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")
        # Older yfinance returns DataFrame
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            val  = cal.loc["Earnings Date"].iloc[0]
            dt   = pd.Timestamp(val).date()
            days = (dt - datetime.date.today()).days
            return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")
    except Exception as e:
        print(f"  Earnings check error: {e}")
    return True, 999, "Unknown"

# ── IV / IVR ──────────────────────────────────────────────
def get_iv_yfinance(ticker):
    """
    Get price, HV, and IV/IVR from yfinance options chain.
    IVR here is term-structure based (not true 52-week IVR).
    Label it as 'est' so user knows to verify in IBKR.
    """
    try:
        df = yf_history_direct(ticker, period_days=252)
        if df.empty:
            return {}
        price = round(float(df["Close"].iloc[-1]), 2)
        hv    = round(float(df["Close"].pct_change().std()) * (252 ** 0.5) * 100, 1)

        tk   = yf_ticker(ticker)
        opts = tk.options
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
                if atm.empty:
                    continue
                iv_raw = float(atm["impliedVolatility"].median())
                iv_pct = round(iv_raw * 100 if iv_raw < 3 else iv_raw, 1)
                if 5 < iv_pct < 300:
                    iv_list.append(iv_pct)
            except Exception:
                continue

        if not iv_list:
            return {"price": price, "hv": hv}

        cur_iv = iv_list[0]
        iv_min = min(iv_list)
        iv_max = max(iv_list)

        # Term-structure IVR (proxy for real IVR — verify in IBKR)
        if len(iv_list) >= 4 and iv_max > iv_min + 2:
            ivr = round(((cur_iv - iv_min) / (iv_max - iv_min)) * 100, 1)
        else:
            ivr = round(min(100, max(0, (cur_iv / hv - 0.7) * 125)), 1) if hv > 0 else 0

        return {
            "price":   price,
            "iv":      cur_iv,
            "hv":      hv,
            "ivr":     max(0, min(100, ivr)),
            "samples": len(iv_list),
        }
    except Exception as e:
        print(f"  get_iv_yfinance error: {e}")
        return {}

def get_iv_data(ticker):
    d   = get_iv_yfinance(ticker)
    ivr = d.get("ivr", 0)
    return {
        "price":  d.get("price"),
        "iv":     d.get("iv", 0),
        "hv":     d.get("hv", 0),
        "ivr":    ivr,
        "source": "yfinance-est",   # Always estimated — verify IVR in IBKR
    }

# ── OPTION PRICES ─────────────────────────────────────────
def get_option_price_yf(ticker, exp_date, strike):
    try:
        tk      = yf_ticker(ticker)
        opts    = tk.options
        best_exp, best_diff = None, 999
        for exp in opts:
            try:
                ed   = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                diff = abs((ed - exp_date).days)
                if diff < best_diff:
                    best_diff = diff
                    best_exp  = exp
            except Exception:
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
        if bid > 0 and ask > bid:
            return round((bid + ask) / 2, 2)
        return round(lv, 2) if lv > 0 else None
    except Exception:
        return None

def get_best_expiry_yf(ticker):
    try:
        tk    = yf_ticker(ticker)
        opts  = tk.options
        today = datetime.date.today()
        best_exp, best_dte = None, None
        for exp in opts:
            try:
                ed  = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (ed - today).days
                if MIN_DTE <= dte <= MAX_DTE:
                    if best_dte is None or abs(dte - 35) < abs(best_dte - 35):
                        best_exp = exp
                        best_dte = dte
            except Exception:
                continue
        return best_exp, best_dte
    except Exception:
        return None, None

# ── SCAN ONE TICKER ───────────────────────────────────────
def scan_ticker(ticker):
    r = {"ticker": ticker, "verdict": "SKIP", "reason": ""}

    # — Price & IV —
    print(f"  Fetching data...")
    d     = get_iv_data(ticker)
    price = d.get("price")
    if not price:
        r["reason"] = "No price data"
        return r

    iv  = d.get("iv", 0)
    hv  = d.get("hv", 0)
    ivr = d.get("ivr", 0)
    src = d.get("source", "?")
    r.update({"price": price, "iv": iv, "hv": hv, "ivr": ivr})
    print(f"  ${price} | IV:{iv}% | HV:{hv}% | IVR(est):{ivr} [{src}]")

    # — Earnings check (FIX: buffer = MAX_DTE = 45d) —
    safe, days_e, date_e = check_earnings(ticker)
    r["earnings"] = f"{date_e} ({days_e}d)"
    print(f"  Earnings: {date_e} ({days_e}d) | buffer={EARNINGS_BUFFER}d")
    if not safe:
        r["reason"] = f"Earnings in {days_e}d ({date_e}) — within trade window"
        return r

    # — IVR filter —
    if ivr < MIN_IV_RANK:
        r["reason"] = f"IVR(est) {ivr:.0f} < {MIN_IV_RANK} [{src}]"
        print(f"  SKIP: IVR too low")
        return r
    print(f"  IVR {ivr:.0f} passes")

    # — Expiry —
    exp_str, dte = get_best_expiry_yf(ticker)
    if not exp_str:
        r["reason"] = f"No expiry {MIN_DTE}-{MAX_DTE}DTE available"
        return r
    exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    exp_disp = exp_date.strftime("%b %d %Y")
    r["expiry"] = exp_disp
    r["dte"]    = dte
    print(f"  Expiry: {exp_disp} ({dte}DTE)")

    # — Strike selection: 10% OTM (FIX: was 12%, too far for decent credit) —
    short_strike = round(price * 0.90 / 2.5) * 2.5
    long_strike  = short_strike - SPREAD_WIDTH
    pct_otm      = round((price - short_strike) / price * 100, 1)
    r["short_strike"] = short_strike
    r["long_strike"]  = long_strike
    print(f"  Strikes: ${short_strike}/${long_strike} ({pct_otm}% OTM)")

    # — Credit —
    sm = get_option_price_yf(ticker, exp_date, short_strike)
    lm = get_option_price_yf(ticker, exp_date, long_strike)
    print(f"  Short mid:${sm} | Long mid:${lm}")
    if sm and lm and sm > lm:
        credit = round(sm - lm, 2)
    elif sm and sm > 0:
        credit = round(sm * 0.45, 2)
    else:
        credit = round(SPREAD_WIDTH * 0.35, 2)   # FIX: was 0.30 → always failed ratio check
    r["credit"] = credit
    print(f"  Credit: ${credit}")

    if credit < SPREAD_WIDTH * MIN_CREDIT_RATIO:
        r["reason"] = f"Credit ${credit:.2f} < min ${SPREAD_WIDTH * MIN_CREDIT_RATIO:.2f}"
        return r

    # — Risk check (FIX: hard $200 / 2% rule enforced here) —
    m        = calc_metrics(credit, SPREAD_WIDTH, 1)
    risk_pct = round(m["nl_usd"] / ACCOUNT_SIZE_USD * 100, 1)

    if m["nl_usd"] > MAX_RISK_USD:
        r["reason"] = f"Max loss ${m['nl_usd']:.0f} exceeds 2% rule (${MAX_RISK_USD:.0f})"
        return r

    r.update({
        "verdict":  "TAKE_IT",
        "np":       m["np_usd"],
        "np_rm":    m["np_rm"],
        "nl":       m["nl_usd"],
        "nl_rm":    m["nl_rm"],
        "fees":     m["fees"],
        "pop":      m["pop"],
        "be":       round(short_strike - credit, 2),
        "risk_pct": risk_pct,
        "pct_otm":  pct_otm,
    })
    print(f"  ✓ TAKE IT | credit=${credit} | max_loss=${m['nl_usd']} ({risk_pct}%) | PoP={m['pop']}%")
    return r

# ── MESSAGES ──────────────────────────────────────────────
def fmt_market(vix, mkt):
    spy = mkt.get("SPY", {})
    qqq = mkt.get("QQQ", {})
    vix_str = f"{vix}" if vix else "N/A (fetch failed)"
    return (
        f"BILLY SCANNER {datetime.date.today()}\n"
        f"================================\n"
        f"SPY: ${spy.get('price','?')} ({spy.get('pct',0):+.1f}%)\n"
        f"QQQ: ${qqq.get('price','?')} ({qqq.get('pct',0):+.1f}%)\n"
        f"VIX: {vix_str} - {vix_label(vix)}\n"
        f"================================"
    )

def fmt_trade(r):
    return (
        f"✅ TRADE: {r['ticker']} - TAKE IT\n"
        f"================================\n"
        f"SELL: ${r['short_strike']} Put\n"
        f"BUY:  ${r['long_strike']} Put\n"
        f"Expiry: {r['expiry']} ({r['dte']}DTE)\n"
        f"Strike OTM: {r.get('pct_otm','?')}%\n"
        f"IVR(est): {r.get('ivr',0):.0f} | IV: {r.get('iv','?')}%\n"
        f"\nECONOMICS (after fees)\n"
        f"Credit:     ${r['credit']:.2f}\n"
        f"Max profit: ${r['np']:.2f} / RM{r['np_rm']:.0f}\n"
        f"Max loss:   ${r['nl']:.2f} / RM{r['nl_rm']:.0f}\n"
        f"Break-even: ${r['be']:.2f}\n"
        f"PoP:        {r['pop']}%\n"
        f"Fees:       ${r['fees']:.2f}\n"
        f"Risk:       {r['risk_pct']}% of account\n"
        f"Earnings:   {r['earnings']}\n"
        f"\nMANAGEMENT\n"
        f"Take profit: ${r['credit']/2:.2f} debit (50%)\n"
        f"Stop loss:   ${r['credit']*2:.2f} debit (2x credit)\n"
        f"Time stop:   close at 21DTE\n"
        f"Breach stop: exit if below ${r['short_strike']}\n"
        f"\n⚠️ IVR is estimated. Verify in IBKR before trading."
    )

def fmt_skip(r):
    return (
        f"⏭ SKIP: {r['ticker']}\n"
        f"Reason: {r['reason']}\n"
        f"IVR(est):{r.get('ivr',0):.0f} | {r.get('earnings','?')}"
    )

def fmt_summary(results, vix):
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    skips = [r["ticker"] for r in results if r["verdict"] == "SKIP"]
    vix_warn = f"\n⚠️ VIX {vix} elevated — use caution" if vix and vix > 25 else ""
    return (
        f"SCAN SUMMARY\n"
        f"================================\n"
        f"Scanned: {len(results)}/{len(WATCHLIST)}\n"
        f"Trades:  {', '.join(takes) if takes else 'None today'}\n"
        f"Skipped: {len(skips)}{vix_warn}\n"
        f"================================\n"
        f"IVR values are ESTIMATES.\n"
        f"Always verify IVR in IBKR before entering.\n"
        f"Only trade if IVR confirmed >30."
    )

# ── MAIN ──────────────────────────────────────────────────
def run():
    now = datetime.datetime.utcnow()
    print("=" * 50)
    print("BILLY OPTIONS SCANNER v3")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"yfinance: {yf.__version__}")
    print(f"Earnings buffer: {EARNINGS_BUFFER}d | Strike OTM: 10% | Fee: ${IBKR_FEE}/leg")
    print("=" * 50)

    print("Priming Yahoo Finance session...")
    get_yf_session()

    send_telegram(
        f"Billy Scanner v3 Starting\n"
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC (MYT+8)\n"
        f"Account: ${ACCOUNT_SIZE_USD:,} | Max risk: ${MAX_RISK_USD:.0f}/trade\n"
        f"Scanning {len(WATCHLIST)} tickers: {', '.join(WATCHLIST)}"
    )

    vix = get_vix()
    mkt = get_market()
    spy = mkt.get("SPY", {})
    print(f"VIX:{vix} | SPY:${spy.get('price','?')}")
    send_telegram(fmt_market(vix, mkt))

    # FIX: warn if VIX data unavailable; only abort if confirmed > 30
    if vix is None:
        send_telegram("⚠️ WARNING: VIX data unavailable. Proceeding with caution.")
    elif vix > 30:
        send_telegram(f"🛑 VIX ALERT: {vix}\nVIX > 30 = High Fear. Stand aside today.")
        return

    results = []
    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print(f"\n[{i}/{len(WATCHLIST)}] {ticker}")
            r = scan_ticker(ticker)
            results.append(r)
            send_telegram(fmt_trade(r) if r["verdict"] == "TAKE_IT" else fmt_skip(r))
            time.sleep(2)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
            continue

    send_telegram(fmt_summary(results, vix))
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    print(f"\nDONE | Scanned:{len(results)} | Trades:{takes or 'None'}")

if __name__ == "__main__":
    run()
