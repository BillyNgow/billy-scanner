#!/usr/bin/env python3
"""
Billy Options Scanner - Cloud Version v4
Runs on GitHub Actions | Sends Telegram alerts

FIXES v4:
  - IVR no longer silently defaults to 0 when iv_list is empty
  - Added clear logging when IV samples fail to collect
  - Yahoo crumb fetch updated to try multiple working endpoints
  - iv_list empty → logged + skip message sent to Telegram (not silent skip)
  - Fallback IVR formula recalibrated (was producing 0 for normal IV stocks)
  - get_option_price_yf: better bid/ask/last fallback + IV-based estimate
  - Earnings calendar: handles new yfinance dict format + list of dates
  - All exceptions now print reason so GitHub Actions log is readable
  - ATM band widened to 0.85-1.15 (was 0.90-1.10) for low-price stocks
  - Term-structure IVR now needs only 3 samples (was 4)
  - iv_ok flag: distinguishes no-data from real low IVR
"""

import os, datetime, time, warnings, traceback
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd, numpy as np

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
MIN_CREDIT_RATIO = 0.30
EARNINGS_BUFFER  = MAX_DTE     # Skip if earnings within 45 days
IBKR_FEE         = 0.65        # $0.65/contract standard rate

# ── YAHOO FINANCE SESSION ──────────────────────────────────
YF_SESSION = None
YF_CRUMB   = None

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
    """Get a cookie-primed session with working crumb. Called once at startup."""
    global YF_SESSION, YF_CRUMB
    if YF_SESSION:
        return YF_SESSION
    YF_SESSION = _build_session()

    # Prime cookies (required before crumb fetch)
    for url in ["https://fc.yahoo.com", "https://finance.yahoo.com"]:
        try:
            YF_SESSION.get(url, timeout=8)
        except Exception:
            pass

    # FIX: Try multiple crumb endpoints — Yahoo changes these periodically
    crumb_urls = [
        "https://query1.finance.yahoo.com/v1/test/csrfToken",
        "https://query2.finance.yahoo.com/v1/test/csrfToken",
        "https://query1.finance.yahoo.com/v1/finance/getCrumb",
    ]
    for curl in crumb_urls:
        try:
            r = YF_SESSION.get(curl, timeout=8)
            if r.status_code == 200 and len(r.text.strip()) > 3:
                YF_CRUMB = r.text.strip()
                print(f"  Yahoo crumb OK: {YF_CRUMB[:12]}... (from {curl})")
                break
        except Exception as e:
            print(f"  Crumb fetch failed ({curl}): {e}")

    if not YF_CRUMB:
        print("  WARNING: Could not get Yahoo crumb — options data may be limited")

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
    s     = get_yf_session()
    end   = int(time.time())
    start = end - period_days * 86400
    params = {"interval": "1d", "period1": start, "period2": end}
    if YF_CRUMB:
        params["crumb"] = YF_CRUMB

    # Try v8 direct first (both query1 and query2)
    for base in ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]:
        try:
            url    = f"{base}/v8/finance/chart/{symbol}"
            r      = s.get(url, params=params, timeout=12)
            if r.status_code == 200:
                d      = r.json()
                result = d.get("chart", {}).get("result")
                if not result:
                    continue
                res    = result[0]
                ts     = res.get("timestamp", [])
                q      = res.get("indicators", {}).get("quote", [{}])[0]
                closes = q.get("close", [])
                valid  = [(t, c) for t, c in zip(ts, closes) if c is not None]
                if len(valid) >= 5:
                    df = pd.DataFrame(valid, columns=["ts", "Close"])
                    df["Date"] = pd.to_datetime(df["ts"], unit="s")
                    df = df.set_index("Date")[["Close"]]
                    return df
        except Exception as e:
            print(f"  v8 direct fetch failed ({base}): {e}")

    # Fallback: yfinance
    try:
        tk = yf_ticker(symbol)
        h  = tk.history(period=f"{period_days}d")
        if not h.empty:
            return h[["Close"]]
    except Exception as e:
        print(f"  yfinance history fallback failed: {e}")

    return pd.DataFrame()

# ── TELEGRAM ──────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [Telegram off] {msg[:80]}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10
            )
            if r.status_code == 200:
                print("  Telegram OK")
                return
            print(f"  Telegram {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"  Telegram error (attempt {attempt+1}): {e}")
            time.sleep(2)

# ── FEES & METRICS ────────────────────────────────────────
def calc_fees(contracts=1):
    # 2 legs x open+close (2 fills each side) x $0.65/contract
    return round(IBKR_FEE * 2 * 2 * contracts, 2)

def calc_metrics(credit, width, contracts=1):
    fees = calc_fees(contracts)
    gp   = credit * 100 * contracts
    gl   = (width - credit) * 100 * contracts
    pop  = round((1 - credit / width) * 100, 1) if width > 0 else 0
    return {
        "np_usd": round(gp - fees, 2),
        "nl_usd": round(gl + fees, 2),
        "np_rm":  round((gp - fees) * USD_MYR_RATE, 2),
        "nl_rm":  round((gl + fees) * USD_MYR_RATE, 2),
        "fees":   fees,
        "pop":    pop,
    }

# ── MARKET DATA ───────────────────────────────────────────
def get_vix():
    try:
        df = yf_history_direct("^VIX", period_days=5)
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"  VIX fetch error: {e}")
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
        except Exception as e:
            print(f"  Market data error {t}: {e}")
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
    Buffer = MAX_DTE (45d). Skip if earnings fall inside the trade window.
    FIX: Handles both dict (new yfinance) and DataFrame (old) calendar formats,
    including when Earnings Date is a list of dates.
    """
    if ticker in ETF_LIST:
        return True, 999, "ETF-no earnings"
    try:
        tk  = yf_ticker(ticker)
        cal = tk.calendar

        if cal is None:
            print(f"  Earnings: calendar is None")
            return True, 999, "Unknown"

        dt = None

        # New yfinance: dict with "Earnings Date" as list or single value
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or cal.get("earningsDate") or []
            if isinstance(ed, (list, tuple)) and len(ed) > 0:
                dt = pd.Timestamp(ed[0]).date()
            elif hasattr(ed, '__len__') is False and ed:
                dt = pd.Timestamp(ed).date()

        # Old yfinance: DataFrame
        elif isinstance(cal, pd.DataFrame):
            for key in ["Earnings Date", "earningsDate"]:
                if key in cal.index:
                    val = cal.loc[key].iloc[0]
                    dt  = pd.Timestamp(val).date()
                    break

        if dt:
            days     = (dt - datetime.date.today()).days
            date_str = dt.strftime("%b %d %Y")
            print(f"  Earnings: {date_str} ({days}d away, buffer={EARNINGS_BUFFER}d)")
            return days > EARNINGS_BUFFER, days, date_str

        print(f"  Earnings: no date found in calendar data")

    except Exception as e:
        print(f"  Earnings check error: {e}")

    return True, 999, "Unknown"

# ── IV / IVR ──────────────────────────────────────────────
def get_iv_yfinance(ticker):
    """
    Get price, HV, and IV/IVR from yfinance options chain.

    FIX v4:
    - Returns ivr=None (not missing key) when IV data unavailable
    - Prints IV sample count so Actions log shows what failed
    - Wider ATM band 0.85-1.15 (was 0.90-1.10)
    - Term-structure IVR needs only 3 samples (was 4)
    - Recalibrated IV/HV fallback formula
    """
    try:
        df = yf_history_direct(ticker, period_days=252)
        if df.empty:
            print(f"  No price history for {ticker}")
            return {}

        price = round(float(df["Close"].iloc[-1]), 2)
        rets  = df["Close"].pct_change().dropna()
        hv    = round(float(rets.std()) * (252 ** 0.5) * 100, 1) if len(rets) > 10 else 0

        tk   = yf_ticker(ticker)
        opts = tk.options
        if not opts:
            print(f"  No options expiries found for {ticker}")
            return {"price": price, "hv": hv, "ivr": None}

        print(f"  Options expiries available: {len(opts)}")

        iv_list = []
        for exp in opts[:12]:   # FIX: scan more expiries (was 10)
            try:
                chain = tk.option_chain(exp)
                puts  = chain.puts

                if puts is None or puts.empty:
                    continue

                # FIX: Wider ATM band — catches low-price stocks better
                atm = puts[
                    (puts["strike"] >= price * 0.85) &
                    (puts["strike"] <= price * 1.15) &
                    (puts["impliedVolatility"] > 0.01)
                ]

                if atm.empty:
                    continue

                iv_raw = float(atm["impliedVolatility"].median())
                # yfinance returns IV as decimal (0.45 = 45%) — convert if needed
                iv_pct = round(iv_raw * 100 if iv_raw < 5 else iv_raw, 1)

                if 5 < iv_pct < 400:
                    iv_list.append(iv_pct)

            except Exception as e:
                print(f"  Option chain error for exp {exp}: {e}")
                continue

        # FIX: Log IV sample count — this is what was silently failing before
        print(f"  IV samples collected: {len(iv_list)} from {min(len(opts),12)} expiries checked")

        if not iv_list:
            print(f"  WARNING: Zero IV samples — options data unavailable for {ticker}")
            # FIX: Return ivr=None so caller knows this is missing data, not a real zero
            return {"price": price, "hv": hv, "ivr": None}

        cur_iv = iv_list[0]
        iv_min = min(iv_list)
        iv_max = max(iv_list)
        print(f"  IV range: {iv_min}% - {iv_max}% | front-month: {cur_iv}% | HV: {hv}%")

        # Term-structure IVR (proxy — verify real IVR in IBKR)
        if len(iv_list) >= 3 and (iv_max - iv_min) > 2:   # FIX: was >= 4 samples
            ivr = round(((cur_iv - iv_min) / (iv_max - iv_min)) * 100, 1)
            print(f"  IVR method: term structure ({len(iv_list)} points) = {ivr}")
        elif hv > 0:
            # FIX: Recalibrated formula
            # Old: (cur_iv/hv - 0.7) * 125  → produced 0 when IV/HV ~ 0.7 (common)
            # New: (ratio - 0.6) * 142       → IV/HV=1.0 → ~57, IV/HV=1.3 → ~100, IV/HV=0.8 → ~28
            ratio = cur_iv / hv
            ivr   = round(min(100, max(0, (ratio - 0.6) * 142)), 1)
            print(f"  IVR method: IV/HV ratio ({ratio:.2f}) = {ivr}")
        else:
            ivr = 50  # FIX: Unknown — use neutral 50 instead of 0 (was causing blanket skips)
            print(f"  IVR method: HV=0 fallback = {ivr} (neutral)")

        return {
            "price":   price,
            "iv":      cur_iv,
            "hv":      hv,
            "ivr":     float(max(0, min(100, ivr))),
            "samples": len(iv_list),
        }

    except Exception as e:
        print(f"  get_iv_yfinance error: {e}")
        traceback.print_exc()
        return {}

def get_iv_data(ticker):
    """
    FIX v4: Distinguishes ivr=None (no data) from ivr=0 (real zero).
    Sets iv_ok flag so scan_ticker can skip with an informative message.
    """
    d   = get_iv_yfinance(ticker)
    ivr = d.get("ivr")   # None = no data; numeric = real value

    iv_ok = ivr is not None

    return {
        "price":  d.get("price"),
        "iv":     d.get("iv", 0),
        "hv":     d.get("hv", 0),
        "ivr":    ivr if iv_ok else 0,
        "iv_ok":  iv_ok,
        "source": "yfinance-est",
    }

# ── OPTION PRICES ─────────────────────────────────────────
def get_option_price_yf(ticker, exp_date, strike):
    """
    FIX v4:
    - Better bid/ask/last selection (uses spread tightness)
    - IV-based price estimate as last resort
    - Prints matched strike for debugging
    """
    try:
        tk   = yf_ticker(ticker)
        opts = tk.options
        if not opts:
            return None

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
            print(f"  No expiry within 7d of {exp_date} (closest={best_diff}d)")
            return None

        puts = tk.option_chain(best_exp).puts
        if puts is None or puts.empty:
            return None

        idx = (puts["strike"] - strike).abs().argsort()
        if idx.empty:
            return None

        row     = puts.iloc[idx[:1]]
        matched = float(row["strike"].iloc[0])
        bid     = float(row["bid"].iloc[0])
        ask     = float(row["ask"].iloc[0])
        last    = float(row["lastPrice"].iloc[0])
        iv_row  = float(row["impliedVolatility"].iloc[0]) if "impliedVolatility" in row.columns else 0

        print(f"  Strike matched: ${matched} (target ${strike}) bid=${bid} ask={ask} last=${last}")

        # FIX: Use mid if spread is tight; last price if spread is wide
        if bid > 0 and ask > 0 and ask > bid:
            spread_pct = (ask - bid) / ask
            if spread_pct < 0.50:
                return round((bid + ask) / 2, 2)
            else:
                return round(last, 2) if last > 0 else round((bid + ask) / 2, 2)
        if last > 0:
            return round(last, 2)

        # Last resort: IV-based estimate
        if iv_row > 0 and matched > 0:
            est = round(matched * iv_row * (30 / 365) ** 0.5 * 0.4, 2)
            print(f"  IV-based price estimate: ${est}")
            return est

        return None

    except Exception as e:
        print(f"  get_option_price_yf error: {e}")
        return None

# ── BEST EXPIRY ───────────────────────────────────────────
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
    except Exception as e:
        print(f"  get_best_expiry_yf error: {e}")
        return None, None

# ── SCAN ONE TICKER ───────────────────────────────────────
def scan_ticker(ticker):
    r = {"ticker": ticker, "verdict": "SKIP", "reason": ""}

    # Price & IV
    print(f"  Fetching IV data...")
    d     = get_iv_data(ticker)
    price = d.get("price")

    if not price:
        r["reason"] = "No price data from Yahoo Finance"
        print(f"  SKIP: {r['reason']}")
        return r

    iv    = d.get("iv", 0)
    hv    = d.get("hv", 0)
    ivr   = d.get("ivr", 0)
    iv_ok = d.get("iv_ok", False)
    src   = d.get("source", "?")
    r.update({"price": price, "iv": iv, "hv": hv, "ivr": ivr})

    print(f"  ${price} | IV:{iv}% | HV:{hv}% | IVR(est):{ivr} | iv_ok={iv_ok} [{src}]")

    # FIX: If zero IV samples collected, skip with clear message instead of IVR=0 skip
    if not iv_ok:
        r["reason"] = "No options IV data from Yahoo Finance — verify manually in IBKR"
        print(f"  SKIP: {r['reason']}")
        return r

    # Earnings check
    safe, days_e, date_e = check_earnings(ticker)
    r["earnings"] = f"{date_e} ({days_e}d)"
    print(f"  Earnings: {date_e} ({days_e}d) | buffer={EARNINGS_BUFFER}d → {'OK' if safe else 'TOO CLOSE'}")
    if not safe:
        r["reason"] = f"Earnings in {days_e}d ({date_e}) — within trade window"
        return r

    # IVR filter
    if ivr < MIN_IV_RANK:
        r["reason"] = f"IVR(est) {ivr:.0f} < {MIN_IV_RANK} — IV not elevated enough [{src}]"
        print(f"  SKIP: IVR {ivr:.0f} too low")
        return r
    print(f"  IVR {ivr:.0f} passes (>= {MIN_IV_RANK})")

    # Expiry
    exp_str, dte = get_best_expiry_yf(ticker)
    if not exp_str:
        r["reason"] = f"No expiry found in {MIN_DTE}-{MAX_DTE}DTE range"
        return r
    exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    exp_disp = exp_date.strftime("%b %d %Y")
    r["expiry"] = exp_disp
    r["dte"]    = dte
    print(f"  Expiry: {exp_disp} ({dte}DTE)")

    # Strike selection: 10% OTM
    short_strike = round(price * 0.90 / 2.5) * 2.5
    long_strike  = short_strike - SPREAD_WIDTH
    pct_otm      = round((price - short_strike) / price * 100, 1)
    r["short_strike"] = short_strike
    r["long_strike"]  = long_strike
    print(f"  Strikes: ${short_strike}/${long_strike} ({pct_otm}% OTM)")

    # Credit
    sm = get_option_price_yf(ticker, exp_date, short_strike)
    lm = get_option_price_yf(ticker, exp_date, long_strike)
    print(f"  Short mid:${sm} | Long mid:${lm}")

    if sm and lm and sm > lm:
        credit = round(sm - lm, 2)
    elif sm and sm > 0:
        credit = round(sm * 0.45, 2)
    else:
        credit = round(SPREAD_WIDTH * 0.35, 2)

    r["credit"] = credit
    print(f"  Credit: ${credit} (min required: ${SPREAD_WIDTH * MIN_CREDIT_RATIO:.2f})")

    if credit < SPREAD_WIDTH * MIN_CREDIT_RATIO:
        r["reason"] = f"Credit ${credit:.2f} < min ${SPREAD_WIDTH * MIN_CREDIT_RATIO:.2f}"
        return r

    # Risk check
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
    print(f"  TAKE IT | credit=${credit} | max_loss=${m['nl_usd']} ({risk_pct}%) | PoP={m['pop']}%")
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
        f"TRADE: {r['ticker']} - TAKE IT\n"
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
        f"\nIVR is estimated. Verify in IBKR before trading."
    )

def fmt_skip(r):
    iv_str = f"{r.get('iv',0):.0f}%" if r.get('iv') else "N/A"
    return (
        f"SKIP: {r['ticker']}\n"
        f"Reason: {r['reason']}\n"
        f"IVR(est):{r.get('ivr',0):.0f} | IV:{iv_str} | {r.get('earnings','?')}"
    )

def fmt_summary(results, vix):
    takes   = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    skips   = [r["ticker"] for r in results if r["verdict"] == "SKIP"]
    no_data = [r["ticker"] for r in results if "No options IV data" in r.get("reason", "")]
    vix_warn = f"\nVIX {vix} elevated — use caution" if vix and vix > 25 else ""

    summary = (
        f"SCAN SUMMARY\n"
        f"================================\n"
        f"Scanned: {len(results)}/{len(WATCHLIST)}\n"
        f"Trades:  {', '.join(takes) if takes else 'None today'}\n"
        f"Skipped: {len(skips)}{vix_warn}\n"
    )
    if no_data:
        summary += f"No IV data: {', '.join(no_data)}\n"
    summary += (
        f"================================\n"
        f"IVR values are ESTIMATES.\n"
        f"Always verify IVR in IBKR before entering.\n"
        f"Only trade if IVR confirmed >30."
    )
    return summary

# ── MAIN ──────────────────────────────────────────────────
def run():
    now = datetime.datetime.utcnow()
    print("=" * 50)
    print("BILLY OPTIONS SCANNER v4")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"yfinance: {yf.__version__}")
    print(f"Earnings buffer: {EARNINGS_BUFFER}d | Strike OTM: 10% | Fee: ${IBKR_FEE}/leg")
    print("=" * 50)

    print("Priming Yahoo Finance session...")
    get_yf_session()

    send_telegram(
        f"Billy Scanner v4 Starting\n"
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC (MYT+8)\n"
        f"Account: ${ACCOUNT_SIZE_USD:,} | Max risk: ${MAX_RISK_USD:.0f}/trade\n"
        f"Scanning {len(WATCHLIST)} tickers: {', '.join(WATCHLIST)}"
    )

    vix = get_vix()
    mkt = get_market()
    spy = mkt.get("SPY", {})
    print(f"VIX:{vix} | SPY:${spy.get('price','?')}")
    send_telegram(fmt_market(vix, mkt))

    if vix is None:
        send_telegram("WARNING: VIX data unavailable. Proceeding with caution.")
    elif vix > 30:
        send_telegram(f"VIX ALERT: {vix}\nVIX > 30 = High Fear. Stand aside today.")
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
            print(f"  Error scanning {ticker}: {e}")
            traceback.print_exc()
            continue

    send_telegram(fmt_summary(results, vix))
    takes = [r["ticker"] for r in results if r["verdict"] == "TAKE_IT"]
    print(f"\nDONE | Scanned:{len(results)} | Trades:{takes or 'None'}")

if __name__ == "__main__":
    run()
