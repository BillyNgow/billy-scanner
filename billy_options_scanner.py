#!/usr/bin/env python3
import os, re, datetime, time, warnings
warnings.filterwarnings("ignore")
import requests, pandas as pd

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
ACCOUNT_SIZE_USD = 10000
MAX_RISK_PCT     = 0.02
USD_MYR_RATE     = 4.40
WATCHLIST        = ["TSLA","PLTR","AMD","MU","NVDA","META","NFLX","AAPL","AMZN","GOOGL","SPY","QQQ","IWM"]
ETF_LIST         = ["SPY","QQQ","IWM","DIA","GLD","TLT"]
MIN_IV_RANK      = 30
MIN_DTE          = 25
MAX_DTE          = 45
SPREAD_WIDTH     = 5
MIN_CREDIT_RATIO = 0.33
EARNINGS_BUFFER  = 14
IBKR_FEE         = 0.79

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [No Telegram]"); return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id":TELEGRAM_CHAT_ID,"text":msg}, timeout=10)
        print("  Telegram OK" if r.status_code==200 else f"  Telegram:{r.status_code}")
    except Exception as e:
        print(f"  Telegram error: {e}")

def calc_fees(c=1): return round(IBKR_FEE*2*2*c,2)

def calc_metrics(credit,width,c=1):
    gp,gl=credit*100*c,(width-credit)*100*c
    fees=calc_fees(c)
    pop=round((1-credit/width)*100,1) if width>0 else 0
    return {"np_usd":round(gp-fees,2),"nl_usd":round(gl+fees,2),
            "np_rm":round((gp-fees)*USD_MYR_RATE,2),
            "nl_rm":round((gl+fees)*USD_MYR_RATE,2),
            "fees":round(fees,2),"pop":pop}

def fetch_yahoo(ticker, path, params=None, retries=3):
    """Fetch from Yahoo Finance with retries and delays."""
    base = "https://query1.finance.yahoo.com"
    urls = [
        f"{base}/v8/finance/{path}",
        f"https://query2.finance.yahoo.com/v8/finance/{path}",
    ]
    for attempt in range(retries):
        for url in urls:
            try:
                time.sleep(3 + attempt * 2)
                r = requests.get(url, headers=HEADERS, params=params, timeout=15)
                if r.status_code == 200:
                    return r.json()
                print(f"  Yahoo {r.status_code} for {ticker}")
            except Exception as e:
                print(f"  Yahoo error: {e}")
    return None

def get_price(ticker):
    """Get current stock price from Yahoo Finance."""
    try:
        data = fetch_yahoo(ticker, f"chart/{ticker}", params={"interval":"1d","range":"5d"})
        if not data:
            return None
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        print(f"  Price error {ticker}: {e}")
        return None

def get_hist_vol(ticker):
    """Get 60-day historical volatility."""
    try:
        data = fetch_yahoo(ticker, f"chart/{ticker}", params={"interval":"1d","range":"90d"})
        if not data:
            return None
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) < 20:
            return None
        import math
        returns = [math.log(closes[i]/closes[i-1]) for i in range(1,len(closes))]
        mean = sum(returns)/len(returns)
        var  = sum((r-mean)**2 for r in returns)/(len(returns)-1)
        hv   = math.sqrt(var) * math.sqrt(252) * 100
        return round(hv, 1)
    except Exception as e:
        print(f"  HV error {ticker}: {e}")
        return None

def get_vix():
    try:
        data = fetch_yahoo("VIX", "chart/%5EVIX", params={"interval":"1d","range":"5d"})
        if not data:
            return None
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[-1], 2) if closes else None
    except: return None

def get_market():
    out = {}
    for t,path in [("SPY","chart/SPY"),("QQQ","chart/QQQ")]:
        try:
            data = fetch_yahoo(t, path, params={"interval":"1d","range":"5d"})
            if not data: continue
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                p  = round(closes[-1], 2)
                pc = round((closes[-1]-closes[-2])/closes[-2]*100, 2)
                out[t] = {"price":p,"pct":pc}
        except: pass
    return out

def vix_label(v):
    if v is None: return "Unknown"
    if v<15: return "Low Fear"
    if v<20: return "Neutral"
    if v<30: return "Elevated"
    return "HIGH FEAR - stand aside"

def check_earnings(ticker):
    if ticker in ETF_LIST: return True,999,"ETF-no earnings"
    try:
        time.sleep(2)
        data = fetch_yahoo(ticker, f"quoteSummary/{ticker}",
                          params={"modules":"calendarEvents"})
        if data:
            events = data.get("quoteSummary",{}).get("result",[])
            if events:
                earnings = events[0].get("calendarEvents",{}).get("earnings",{})
                dates    = earnings.get("earningsDate",[])
                if dates:
                    ts   = dates[0].get("raw",0)
                    dt   = datetime.datetime.fromtimestamp(ts).date()
                    days = (dt - datetime.date.today()).days
                    return days>EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")
    except Exception as e:
        print(f"  Earnings error {ticker}: {e}")
    return True, 999, "Unknown"

def get_ivr_barchart(ticker):
    try:
        time.sleep(3)
        url = f"https://www.barchart.com/stocks/quotes/{ticker}/overview"
        h   = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r   = requests.get(url, headers=h, timeout=15)
        if r.status_code != 200: return None
        m = re.search(r"ivRank.*?(\d+\.?\d*)", r.text)
        if m: return round(float(m.group(1)),1)
        m = re.search(r"IV Rank[^\d]*(\d+\.?\d*)", r.text)
        if m: return round(float(m.group(1)),1)
        return None
    except: return None

def get_iv_from_options(ticker, price):
    """Get IV from Yahoo Finance options chain."""
    try:
        time.sleep(3)
        data = fetch_yahoo(ticker, f"options/{ticker}")
        if not data: return None
        result = data.get("optionChain",{}).get("result",[])
        if not result: return None
        opts   = result[0]
        puts   = opts.get("options",[{}])[0].get("puts",[])
        if not puts: return None
        atm_puts = [p for p in puts
                    if p.get("strike",0) >= price*0.90
                    and p.get("strike",0) <= price*1.10
                    and p.get("impliedVolatility",0) > 0.01]
        if not atm_puts: return None
        ivs = [p["impliedVolatility"] for p in atm_puts]
        iv  = (sum(ivs)/len(ivs)) * 100
        return round(iv, 1)
    except Exception as e:
        print(f"  IV options error: {e}")
        return None

def get_options_expiries(ticker):
    """Get available option expiry dates."""
    try:
        time.sleep(2)
        data = fetch_yahoo(ticker, f"options/{ticker}")
        if not data: return []
        result = data.get("optionChain",{}).get("result",[])
        if not result: return []
        return result[0].get("expirationDates",[])
    except: return []

def get_option_chain(ticker, expiry_ts):
    """Get puts for a specific expiry timestamp."""
    try:
        time.sleep(3)
        data = fetch_yahoo(ticker, f"options/{ticker}",
                          params={"date": expiry_ts})
        if not data: return []
        result = data.get("optionChain",{}).get("result",[])
        if not result: return []
        return result[0].get("options",[{}])[0].get("puts",[])
    except: return []

def get_iv_data(ticker):
    """Get price, IV, HV, IVR for a ticker."""
    price = get_price(ticker)
    if not price:
        return {}

    hv  = get_hist_vol(ticker)
    iv  = get_iv_from_options(ticker, price)
    bvr = get_ivr_barchart(ticker)

    if bvr is not None:
        ivr = bvr
        src = "Barchart"
    elif iv and hv and hv > 0:
        ratio = iv / hv
        ivr   = round(min(100, max(0, (ratio-0.7)*125)), 1)
        src   = "yfinance-est"
    else:
        ivr = 0
        src = "unknown"

    return {
        "price": price,
        "iv":    iv or 0,
        "hv":    hv or 0,
        "ivr":   ivr,
        "source": src
    }

def find_best_expiry(ticker):
    """Find best expiry in 25-45 DTE range."""
    expiries = get_options_expiries(ticker)
    today    = datetime.date.today()
    best_ts  = None
    best_dte = None

    for ts in expiries:
        try:
            ed  = datetime.datetime.fromtimestamp(ts).date()
            dte = (ed - today).days
            if MIN_DTE <= dte <= MAX_DTE:
                if best_dte is None or abs(dte-35) < abs(best_dte-35):
                    best_ts  = ts
                    best_dte = dte
        except: continue

    if not best_ts:
        return None, None, None

    exp_date = datetime.datetime.fromtimestamp(best_ts).date()
    return best_ts, best_dte, exp_date.strftime("%b %d %Y")

def get_credit(ticker, expiry_ts, short_strike, long_strike):
    """Get net credit for bull put spread."""
    puts = get_option_chain(ticker, expiry_ts)
    if not puts:
        return round(SPREAD_WIDTH * 0.30, 2)

    short_mid = None
    long_mid  = None

    for p in puts:
        strike = p.get("strike",0)
        bid    = p.get("bid",0)
        ask    = p.get("ask",0)
        last   = p.get("lastPrice",0)

        if abs(strike - short_strike) < 1.5:
            if bid>0 and ask>0 and ask>bid:
                short_mid = round((bid+ask)/2, 2)
            elif last>0:
                short_mid = round(last, 2)

        if abs(strike - long_strike) < 1.5:
            if bid>0 and ask>0 and ask>bid:
                long_mid = round((bid+ask)/2, 2)
            elif last>0:
                long_mid = round(last, 2)

    if short_mid and long_mid and short_mid > long_mid:
        return round(short_mid - long_mid, 2)
    elif short_mid and short_mid > 0:
        return round(short_mid * 0.45, 2)
    return round(SPREAD_WIDTH * 0.30, 2)

def scan_ticker(ticker):
    print(f"  Getting data...")
    r = {"ticker":ticker,"verdict":"SKIP","reason":""}

    d = get_iv_data(ticker)
    if not d or not d.get("price"):
        r["reason"] = "No price data"
        return r

    price = d["price"]
    iv    = d.get("iv", 0)
    hv    = d.get("hv", 0)
    ivr   = d.get("ivr", 0)
    src   = d.get("source", "?")
    r.update({"price":price,"iv":iv,"hv":hv,"ivr":ivr})
    print(f"  ${price} | IV:{iv}% | HV:{hv}% | IVR:{ivr} [{src}]")

    safe, days_e, date_e = check_earnings(ticker)
    r["earnings"] = f"{date_e} ({days_e}d)"
    print(f"  Earnings: {date_e} ({days_e}d)")
    if not safe:
        r["reason"] = f"Earnings in {days_e}d ({date_e})"
        return r

    if ivr < MIN_IV_RANK:
        r["reason"] = f"IVR {ivr:.0f}<{MIN_IV_RANK} [{src}]"
        print(f"  SKIP: IVR too low")
        return r
    print(f"  IVR {ivr:.0f} PASSES")

    expiry_ts, dte, exp_str = find_best_expiry(ticker)
    if not expiry_ts:
        r["reason"] = f"No expiry {MIN_DTE}-{MAX_DTE}DTE"
        return r
    r["expiry"] = exp_str
    r["dte"]    = dte
    print(f"  Expiry: {exp_str} ({dte}DTE)")

    short_strike = round(price * 0.88 / 2.5) * 2.5
    long_strike  = short_strike - SPREAD_WIDTH
    r["short_strike"] = short_strike
    r["long_strike"]  = long_strike
    print(f"  Strikes: ${short_strike}/${long_strike}")

    credit = get_credit(ticker, expiry_ts, short_strike, long_strike)
    r["credit"] = credit
    print(f"  Credit: ${credit}")

    if credit < SPREAD_WIDTH * MIN_CREDIT_RATIO:
        r["reason"] = f"Credit ${credit:.2f} too low"
        return r

    m        = calc_metrics(credit, SPREAD_WIDTH, 1)
    risk_pct = round(m["nl_usd"] / ACCOUNT_SIZE_USD * 100, 1)
    r.update({
        "verdict":"TAKE_IT","np":m["np_usd"],"np_rm":m["np_rm"],
        "nl":m["nl_usd"],"nl_rm":m["nl_rm"],"fees":m["fees"],
        "pop":m["pop"],"be":round(short_strike-credit,2),
        "risk_pct":risk_pct
    })
    print(f"  TAKE IT | ${credit} | Loss:${m['nl_usd']} | PoP:{m['pop']}%")
    return r

def fmt_market(vix,mkt):
    spy=mkt.get("SPY",{}); qqq=mkt.get("QQQ",{})
    return (f"BILLY SCANNER {datetime.date.today()}\n================================\n"
            f"SPY: ${spy.get('price','?')} ({spy.get('pct',0):+.1f}%)\n"
            f"QQQ: ${qqq.get('price','?')} ({qqq.get('pct',0):+.1f}%)\n"
            f"VIX: {vix} - {vix_label(vix)}\n================================")

def fmt_trade(r):
    warn = "WARNING: Above 2% rule" if r["risk_pct"]>5 else "Within risk limits"
    return (f"TRADE: {r['ticker']} - TAKE IT\n================================\n"
            f"SELL: ${r['short_strike']} Put\nBUY:  ${r['long_strike']} Put\n"
            f"Expiry: {r['expiry']} ({r['dte']}DTE)\n"
            f"IVR: {r.get('ivr',0):.0f} | IV:{r.get('iv','?')}%\n\n"
            f"ECONOMICS\nCredit: ${r['credit']:.2f}\n"
            f"Profit: ${r['np']:.2f} / RM{r['np_rm']:.0f}\n"
            f"Loss:   ${r['nl']:.2f} / RM{r['nl_rm']:.0f}\n"
            f"BE: ${r['be']:.2f} | PoP:{r['pop']}%\n"
            f"Fees: ${r['fees']:.2f}\n\n"
            f"1 contract | {r['risk_pct']}% risk\n{warn}\n"
            f"Earnings: {r['earnings']}\n\n"
            f"MANAGEMENT\n"
            f"Profit: ${r['credit']/2:.2f} debit (50%)\n"
            f"Stop:   ${r['credit']*2:.2f} debit (2x)\n"
            f"Close by 21DTE\n"
            f"Exit if below ${r['short_strike']}\n\n"
            f"Open IBKR to verify and trade")

def fmt_skip(r):
    return (f"SKIP: {r['ticker']}\n"
            f"Reason: {r['reason']}\n"
            f"IVR:{r.get('ivr',0):.0f} | {r.get('earnings','?')}")

def fmt_summary(results,vix):
    takes=[r["ticker"] for r in results if r["verdict"]=="TAKE_IT"]
    skips=[r["ticker"] for r in results if r["verdict"]=="SKIP"]
    warn=f"\nVIX {vix} elevated" if vix and vix>25 else ""
    return (f"SCAN SUMMARY\n================================\n"
            f"Scanned: {len(results)}/{len(WATCHLIST)}\n"
            f"Trades:  {', '.join(takes) if takes else 'None today'}\n"
            f"Skipped: {len(skips)}{warn}\n================================\n"
            f"Verify IVR in IBKR before trading.\n"
            f"Only enter if IVR confirmed >30.")

def run():
    now = datetime.datetime.utcnow()
    print("="*50)
    print("BILLY OPTIONS SCANNER - CLOUD v2")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Watchlist: {len(WATCHLIST)} tickers")
    print("="*50)

    time.sleep(5)  # Let GitHub runner warm up

    send_telegram(
        f"Billy Scanner Starting\n"
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC (9:30 PM MYT)\n"
        f"Account: ${ACCOUNT_SIZE_USD:,} USD\n"
        f"Scanning {len(WATCHLIST)} tickers:\n"
        f"{', '.join(WATCHLIST)}"
    )

    vix = get_vix()
    mkt = get_market()
    print(f"VIX:{vix} | SPY:${mkt.get('SPY',{}).get('price','?')}")
    send_telegram(fmt_market(vix, mkt))

    if vix and vix > 30:
        send_telegram(f"VIX ALERT: {vix}\nVIX > 30 = High Fear\nStand aside today.")
        return

    results = []
    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print(f"\n[{i}/{len(WATCHLIST)}] {ticker}")
            r = scan_ticker(ticker)
            results.append(r)
            send_telegram(fmt_trade(r) if r["verdict"]=="TAKE_IT" else fmt_skip(r))
            time.sleep(3)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
            continue

    send_telegram(fmt_summary(results, vix))
    takes = [r["ticker"] for r in results if r["verdict"]=="TAKE_IT"]
    print(f"\nDONE | Scanned:{len(results)} | Trades:{takes or 'None'}")

if __name__ == "__main__":
    run()
