#!/usr/bin/env python3
import os, re, datetime, time, warnings, math
warnings.filterwarnings("ignore")
import requests, pandas as pd

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
ALPHAVANTAGE_KEY  = os.environ.get("ALPHAVANTAGE_KEY", "")

ACCOUNT_SIZE_USD  = 10000
MAX_RISK_PCT      = 0.02
USD_MYR_RATE      = 4.40
WATCHLIST         = ["TSLA","PLTR","AMD","MU","NVDA","META","NFLX","AAPL","AMZN","GOOGL","SPY","QQQ","IWM"]
ETF_LIST          = ["SPY","QQQ","IWM","DIA","GLD","TLT"]
MIN_IV_RANK       = 30
MIN_DTE           = 25
MAX_DTE           = 45
SPREAD_WIDTH      = 5
MIN_CREDIT_RATIO  = 0.33
EARNINGS_BUFFER   = 14
IBKR_FEE          = 0.79
AV_BASE           = "https://www.alphavantage.co/query"

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

def av_request(params, delay=12):
    """Alpha Vantage API request with rate limit delay."""
    time.sleep(delay)
    params["apikey"] = ALPHAVANTAGE_KEY
    try:
        r = requests.get(AV_BASE, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if "Note" in data:
                print("  AV rate limit hit - waiting 60s")
                time.sleep(60)
                r = requests.get(AV_BASE, params=params, timeout=15)
                data = r.json()
            if "Information" in data:
                print("  AV API limit reached today")
                return None
            return data
        return None
    except Exception as e:
        print(f"  AV error: {e}")
        return None

def get_price_and_hv(ticker):
    """Get price and 30-day HV from Alpha Vantage daily data."""
    print(f"  Getting price+HV from Alpha Vantage...")
    data = av_request({
        "function": "TIME_SERIES_DAILY",
        "symbol":   ticker,
        "outputsize":"compact"
    })
    if not data:
        return None, None

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        print(f"  No time series for {ticker}")
        return None, None

    dates  = sorted(ts.keys(), reverse=True)
    closes = []
    for d in dates[:60]:
        try:
            closes.append(float(ts[d]["4. close"]))
        except: continue

    if not closes:
        return None, None

    price = closes[0]

    if len(closes) >= 20:
        returns = [math.log(closes[i]/closes[i+1])
                   for i in range(len(closes)-1)]
        mean = sum(returns)/len(returns)
        var  = sum((r-mean)**2 for r in returns)/(len(returns)-1)
        hv   = round(math.sqrt(var)*math.sqrt(252)*100, 1)
    else:
        hv = None

    return round(price, 2), hv

def get_vix():
    """Get VIX from Alpha Vantage."""
    data = av_request({
        "function": "TIME_SERIES_DAILY",
        "symbol":   "VIX",
        "outputsize":"compact"
    })
    if not data:
        return None
    ts = data.get("Time Series (Daily)", {})
    if not ts:
        return None
    latest = sorted(ts.keys(), reverse=True)[0]
    try:
        return round(float(ts[latest]["4. close"]), 2)
    except: return None

def get_market():
    """Get SPY and QQQ prices."""
    out = {}
    for t in ["SPY","QQQ"]:
        data = av_request({
            "function": "TIME_SERIES_DAILY",
            "symbol":   t,
            "outputsize":"compact"
        })
        if not data: continue
        ts = data.get("Time Series (Daily)", {})
        if not ts: continue
        dates = sorted(ts.keys(), reverse=True)
        try:
            p1  = float(ts[dates[0]]["4. close"])
            p2  = float(ts[dates[1]]["4. close"])
            pc  = round((p1-p2)/p2*100, 2)
            out[t] = {"price":round(p1,2),"pct":pc}
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
        data = av_request({
            "function": "EARNINGS_CALENDAR",
            "symbol":   ticker,
            "horizon":  "3month"
        }, delay=12)
        if data:
            import csv, io
            reader = csv.DictReader(io.StringIO(data.text
                     if hasattr(data,"text") else str(data)))
            for row in reader:
                if row.get("symbol","").upper() == ticker.upper():
                    rd = row.get("reportDate","")
                    if rd:
                        dt   = datetime.datetime.strptime(rd,"%Y-%m-%d").date()
                        days = (dt-datetime.date.today()).days
                        return days>EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")
    except Exception as e:
        print(f"  Earnings error: {e}")
    return True, 999, "Unknown"

def get_earnings_av(ticker):
    """Get earnings date via Alpha Vantage CSV endpoint."""
    if ticker in ETF_LIST: return True,999,"ETF-no earnings"
    try:
        time.sleep(12)
        url    = f"{AV_BASE}?function=EARNINGS_CALENDAR&symbol={ticker}&horizon=3month&apikey={ALPHAVANTAGE_KEY}"
        r      = requests.get(url, timeout=15)
        if r.status_code != 200: return True,999,"Unknown"
        import csv,io
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            if row.get("symbol","").upper()==ticker.upper():
                rd=row.get("reportDate","")
                if rd:
                    dt=datetime.datetime.strptime(rd,"%Y-%m-%d").date()
                    days=(dt-datetime.date.today()).days
                    return days>EARNINGS_BUFFER,days,dt.strftime("%b %d %Y")
    except Exception as e:
        print(f"  Earnings AV error: {e}")
    return True,999,"Unknown"

def get_ivr_barchart(ticker):
    """Get IV Rank from Barchart."""
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

def get_iv_data(ticker):
    """Get price, HV, IV, IVR for ticker."""
    price, hv = get_price_and_hv(ticker)
    if not price:
        return {}

    bvr = get_ivr_barchart(ticker)
    if bvr is not None:
        ivr = bvr
        src = "Barchart"
    elif hv and hv > 0:
        ivr = round(min(100,max(0,(30/hv-0.7)*125)),1)
        src = "estimated"
    else:
        ivr = 0
        src = "unknown"

    return {
        "price":  price,
        "hv":     hv or 0,
        "iv":     0,
        "ivr":    ivr,
        "source": src
    }

def get_option_estimate(price, dte):
    """
    Estimate option prices using Black-Scholes approximation.
    Used when live option chain not available.
    """
    try:
        import math
        iv_est  = 0.35
        t       = dte / 365.0
        short_s = round(price * 0.88 / 2.5) * 2.5
        long_s  = short_s - SPREAD_WIDTH

        def bs_put(S, K, T, sigma):
            if T <= 0: return max(0, K-S)
            d1 = (math.log(S/K) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
            d2 = d1 - sigma*math.sqrt(T)
            from scipy.stats import norm
            return K*math.exp(0)*norm.cdf(-d2) - S*norm.cdf(-d1)

        try:
            from scipy.stats import norm
            short_p = bs_put(price, short_s, t, iv_est)
            long_p  = bs_put(price, long_s,  t, iv_est)
            credit  = round(short_p - long_p, 2)
        except:
            credit = round(SPREAD_WIDTH * 0.28, 2)

        return short_s, long_s, max(0.10, credit)
    except:
        short_s = round(price * 0.88 / 2.5) * 2.5
        long_s  = short_s - SPREAD_WIDTH
        return short_s, long_s, round(SPREAD_WIDTH*0.28, 2)

def scan_ticker(ticker):
    print(f"  Getting IV data...")
    r = {"ticker":ticker,"verdict":"SKIP","reason":""}

    d = get_iv_data(ticker)
    if not d or not d.get("price"):
        r["reason"] = "No price data"
        return r

    price = d["price"]
    hv    = d.get("hv", 0)
    ivr   = d.get("ivr", 0)
    src   = d.get("source","?")
    r.update({"price":price,"hv":hv,"ivr":ivr})
    print(f"  ${price} | HV:{hv}% | IVR:{ivr} [{src}]")

    safe,days_e,date_e = get_earnings_av(ticker)
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

    # Estimate expiry (nearest Friday in DTE range)
    today = datetime.date.today()
    target_dte = 35
    target_date = today + datetime.timedelta(days=target_dte)
    # Round to nearest Friday
    days_to_fri = (4 - target_date.weekday()) % 7
    exp_date = target_date + datetime.timedelta(days=days_to_fri)
    dte      = (exp_date - today).days
    exp_str  = exp_date.strftime("%b %d %Y")
    r["expiry"] = exp_str
    r["dte"]    = dte
    print(f"  Expiry est: {exp_str} ({dte}DTE)")

    short_strike, long_strike, credit = get_option_estimate(price, dte)
    r["short_strike"] = short_strike
    r["long_strike"]  = long_strike
    r["credit"]       = credit
    print(f"  Strikes: ${short_strike}/${long_strike} | Credit: ${credit}")

    if credit < SPREAD_WIDTH * MIN_CREDIT_RATIO:
        r["reason"] = f"Credit ${credit:.2f} too low"
        return r

    m        = calc_metrics(credit, SPREAD_WIDTH, 1)
    risk_pct = round(m["nl_usd"]/ACCOUNT_SIZE_USD*100, 1)
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
    warn="WARNING: Above 2% rule" if r["risk_pct"]>5 else "Within risk limits"
    return (f"TRADE: {r['ticker']} - TAKE IT\n================================\n"
            f"SELL: ${r['short_strike']} Put\nBUY:  ${r['long_strike']} Put\n"
            f"Expiry: {r['expiry']} ({r['dte']}DTE)\n"
            f"IVR: {r.get('ivr',0):.0f} | HV:{r.get('hv','?')}%\n\n"
            f"ECONOMICS (estimated)\n"
            f"Credit: ${r['credit']:.2f}\n"
            f"Profit: ${r['np']:.2f} / RM{r['np_rm']:.0f}\n"
            f"Loss:   ${r['nl']:.2f} / RM{r['nl_rm']:.0f}\n"
            f"BE: ${r['be']:.2f} | PoP:{r['pop']}%\n"
            f"Fees: ${r['fees']:.2f}\n\n"
            f"1 contract | {r['risk_pct']}% risk\n{warn}\n"
            f"Earnings: {r['earnings']}\n\n"
            f"IMPORTANT: Credit is estimated.\n"
            f"Open IBKR to verify live chain\n"
            f"before placing any trade.")

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
            f"Credits are ESTIMATED.\n"
            f"Always verify in IBKR TWS\n"
            f"before placing any trade.\n"
            f"Only enter if IVR confirmed >30.")

def run():
    now = datetime.datetime.utcnow()
    print("="*50)
    print("BILLY OPTIONS SCANNER - CLOUD v3")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Data: Alpha Vantage + Barchart")
    print(f"Watchlist: {len(WATCHLIST)} tickers")
    print("="*50)

    time.sleep(5)

    send_telegram(
        f"Billy Scanner Starting\n"
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"9:30 PM MYT\n"
        f"Scanning {len(WATCHLIST)} tickers:\n"
        f"{', '.join(WATCHLIST)}"
    )

    print("\nGetting VIX...")
    vix = get_vix()
    print(f"VIX: {vix}")

    print("Getting market data...")
    mkt = get_market()
    send_telegram(fmt_market(vix, mkt))

    if vix and vix > 30:
        send_telegram(
            f"VIX ALERT: {vix}\n"
            f"VIX > 30 = High Fear\n"
            f"Stand aside today."
        )
        return

    results = []
    for i, ticker in enumerate(WATCHLIST, 1):
        try:
            print(f"\n[{i}/{len(WATCHLIST)}] {ticker}")
            r = scan_ticker(ticker)
            results.append(r)
            send_telegram(
                fmt_trade(r) if r["verdict"]=="TAKE_IT"
                else fmt_skip(r)
            )
            time.sleep(3)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
            continue

    send_telegram(fmt_summary(results, vix))
    takes=[r["ticker"] for r in results if r["verdict"]=="TAKE_IT"]
    print(f"\nDONE | Scanned:{len(results)} | Trades:{takes or 'None'}")

if __name__=="__main__":
    run()
