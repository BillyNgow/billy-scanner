#!/usr/bin/env python3
"""
Billy Options Scanner - Cloud Version
Runs on GitHub Actions (no IBKR needed)
Scans IV Rank, earnings, market conditions
Sends Telegram alerts when trades qualify
"""

import os, re, datetime, time, warnings
warnings.filterwarnings("ignore")
import requests, yfinance as yf, pandas as pd

# ── CONFIG (from GitHub Secrets) ──────────────────────────
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

# ── TELEGRAM ──────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [Telegram not configured]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id":TELEGRAM_CHAT_ID,"text":msg}, timeout=10)
        print("  Telegram OK" if r.status_code==200 else f"  Telegram err:{r.status_code}")
    except Exception as e:
        print(f"  Telegram error: {e}")

# ── FEES ──────────────────────────────────────────────────
def calc_fees(c=1): return round(IBKR_FEE*2*2*c, 2)

def calc_metrics(credit, width, c=1):
    gp,gl = credit*100*c, (width-credit)*100*c
    fees  = calc_fees(c)
    pop   = round((1-credit/width)*100,1) if width>0 else 0
    return {
        "np_usd":round(gp-fees,2), "nl_usd":round(gl+fees,2),
        "np_rm": round((gp-fees)*USD_MYR_RATE,2),
        "nl_rm": round((gl+fees)*USD_MYR_RATE,2),
        "fees":  round(fees,2), "pop":pop
    }

# ── MARKET ────────────────────────────────────────────────
def get_vix():
    try:
        h=yf.Ticker("^VIX").history(period="5d")
        return round(float(h["Close"].iloc[-1]),2) if not h.empty else None
    except: return None

def get_market():
    out={}
    for t in ["SPY","QQQ"]:
        try:
            h=yf.Ticker(t).history(period="5d")
            if len(h)>=2:
                p=round(float(h["Close"].iloc[-1]),2)
                pc=round((p-float(h["Close"].iloc[-2]))/float(h["Close"].iloc[-2])*100,2)
                out[t]={"price":p,"pct":pc}
        except: pass
    return out

def vix_label(v):
    if v is None: return "Unknown"
    if v<15: return "Low Fear"
    if v<20: return "Neutral"
    if v<30: return "Elevated"
    return "HIGH FEAR - stand aside"

# ── EARNINGS ──────────────────────────────────────────────
# FIX: yfinance .calendar API changed — now returns a DataFrame or dict depending on version.
# This version handles both safely.
def check_earnings(ticker):
    if ticker in ETF_LIST:
        return True, 999, "ETF-no earnings"
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return True, 999, "Unknown"

        # New yfinance: calendar is a dict like {"Earnings Date": [Timestamp, ...]}
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date", [])
            if ed:
                dt = pd.Timestamp(ed[0]).date()
                days = (dt - datetime.date.today()).days
                return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")

        # Older yfinance: calendar is a DataFrame with dates as columns
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"].iloc[0]
                dt = pd.Timestamp(val).date()
                days = (dt - datetime.date.today()).days
                return days > EARNINGS_BUFFER, days, dt.strftime("%b %d %Y")

    except Exception as e:
        print(f"  Earnings check error: {e}")

    return True, 999, "Unknown"

# ── IVR FROM BARCHART ─────────────────────────────────────
# NOTE: Barchart now returns 403 for scraping — this will always return None.
# Keeping for structure but the yfinance fallback is what actually runs.
def get_ivr_barchart(ticker):
    try:
        url=f"https://www.barchart.com/stocks/quotes/{ticker}/overview"
        h={"User-Agent":"Mozilla/5.0 (Macintosh) AppleWebKit/537.36"}
        r=requests.get(url,headers=h,timeout=12)
        if r.status_code!=200:
            print(f"  Barchart {r.status_code} for {ticker} (blocked)")
            return None
        m=re.search(r"ivRank.*?(\d+\.?\d*)",r.text)
        if m: return round(float(m.group(1)),1)
        m=re.search(r"IV Rank[^\d]*(\d+\.?\d*)",r.text)
        if m: return round(float(m.group(1)),1)
        return None
    except: return None

# ── IV FROM YFINANCE ──────────────────────────────────────
def get_iv_yfinance(ticker):
    try:
        tk=yf.Ticker(ticker)
        hist=tk.history(period="60d")
        if hist.empty:
            print(f"  WARNING: yfinance returned empty history for {ticker}")
            return {}
        price=round(float(hist["Close"].iloc[-1]),2)
        hv=round(float(hist["Close"].pct_change().std())*(252**0.5)*100,1)
        opts=tk.options
        if not opts: return {"price":price,"hv":hv}
        iv_list=[]
        for exp in opts[:10]:
            try:
                puts=tk.option_chain(exp).puts
                atm=puts[(puts["strike"]>=price*0.90)&(puts["strike"]<=price*1.10)&(puts["impliedVolatility"]>0.01)]
                if atm.empty: continue
                iv_raw=float(atm["impliedVolatility"].median())
                iv_pct=round(iv_raw*100 if iv_raw<3 else iv_raw,1)
                if 5<iv_pct<300: iv_list.append(iv_pct)
            except: continue
        if not iv_list: return {"price":price,"hv":hv}
        cur_iv=iv_list[0]; iv_min=min(iv_list); iv_max=max(iv_list)
        if len(iv_list)>=4 and iv_max>iv_min+2:
            ivr=round(((cur_iv-iv_min)/(iv_max-iv_min))*100,1)
        else:
            ivr=round(min(100,max(0,(cur_iv/hv-0.7)*125)),1) if hv>0 else 0
        return {"price":price,"iv":cur_iv,"hv":hv,"ivr":max(0,min(100,ivr)),"samples":len(iv_list)}
    except Exception as e:
        print(f"  get_iv_yfinance error: {e}")
        return {}

def get_iv_data(ticker):
    yfd=get_iv_yfinance(ticker)
    bvr=get_ivr_barchart(ticker)
    ivr=bvr if bvr is not None else yfd.get("ivr",0)
    src="Barchart" if bvr is not None else "yfinance"
    return {"price":yfd.get("price"),"iv":yfd.get("iv",0),
            "hv":yfd.get("hv",0),"ivr":ivr,"source":src}

# ── OPTION PRICES ─────────────────────────────────────────
def get_option_price_yf(ticker, exp_date, strike):
    try:
        tk=yf.Ticker(ticker); opts=tk.options
        best_exp=None; best_diff=999
        for exp in opts:
            try:
                ed=datetime.datetime.strptime(exp,"%Y-%m-%d").date()
                diff=abs((ed-exp_date).days)
                if diff<best_diff: best_diff=diff; best_exp=exp
            except: continue
        if not best_exp or best_diff>7: return None
        puts=tk.option_chain(best_exp).puts
        if puts.empty: return None
        row=puts.iloc[(puts["strike"]-strike).abs().argsort()[:1]]
        if row.empty: return None
        bid=float(row["bid"].iloc[0]); ask=float(row["ask"].iloc[0])
        lv=float(row["lastPrice"].iloc[0])
        if bid>0 and ask>0 and ask>bid: return round((bid+ask)/2,2)
        return round(lv,2) if lv>0 else None
    except: return None

def get_best_expiry_yf(ticker):
    try:
        tk=yf.Ticker(ticker); opts=tk.options
        today=datetime.date.today()
        best_exp=None; best_dte=None
        for exp in opts:
            try:
                ed=datetime.datetime.strptime(exp,"%Y-%m-%d").date()
                dte=(ed-today).days
                if MIN_DTE<=dte<=MAX_DTE:
                    if best_dte is None or abs(dte-35)<abs(best_dte-35):
                        best_exp=exp; best_dte=dte
            except: continue
        return best_exp,best_dte
    except: return None,None

# ── SCAN ONE TICKER ───────────────────────────────────────
def scan_ticker(ticker):
    print(f"  Getting IV...")
    r={"ticker":ticker,"verdict":"SKIP","reason":""}
    d=get_iv_data(ticker)
    price=d.get("price")
    if not price: r["reason"]="No price data"; return r
    iv=d.get("iv",0); hv=d.get("hv",0); ivr=d.get("ivr",0); src=d.get("source","?")
    r.update({"price":price,"iv":iv,"hv":hv,"ivr":ivr})
    print(f"  ${price} | IV:{iv}% | HV:{hv}% | IVR:{ivr} [{src}]")
    safe,days_e,date_e=check_earnings(ticker)
    r["earnings"]=f"{date_e} ({days_e}d)"
    print(f"  Earnings: {date_e} ({days_e}d)")
    if not safe: r["reason"]=f"Earnings in {days_e}d ({date_e})"; return r
    if ivr<MIN_IV_RANK: r["reason"]=f"IVR {ivr:.0f}<{MIN_IV_RANK} [{src}]"; print(f"  SKIP IVR"); return r
    print(f"  IVR {ivr:.0f} PASSES")
    exp_str,dte=get_best_expiry_yf(ticker)
    if not exp_str: r["reason"]=f"No expiry {MIN_DTE}-{MAX_DTE}DTE"; return r
    exp_date=datetime.datetime.strptime(exp_str,"%Y-%m-%d").date()
    exp_disp=exp_date.strftime("%b %d %Y"); r["expiry"]=exp_disp; r["dte"]=dte
    print(f"  Expiry: {exp_disp} ({dte}DTE)")
    short_strike=round(price*0.88/2.5)*2.5
    long_strike=short_strike-SPREAD_WIDTH
    r["short_strike"]=short_strike; r["long_strike"]=long_strike
    print(f"  Strikes: ${short_strike}/${long_strike}")
    sm=get_option_price_yf(ticker,exp_date,short_strike)
    lm=get_option_price_yf(ticker,exp_date,long_strike)
    print(f"  Short:${sm} Long:${lm}")
    if sm and lm and sm>lm: credit=round(sm-lm,2)
    elif sm and sm>0: credit=round(sm*0.45,2)
    else: credit=round(SPREAD_WIDTH*0.30,2)
    r["credit"]=credit; print(f"  Credit: ${credit}")
    if credit<SPREAD_WIDTH*MIN_CREDIT_RATIO:
        r["reason"]=f"Credit ${credit:.2f} too low"; return r
    m=calc_metrics(credit,SPREAD_WIDTH,1)
    risk_pct=round(m["nl_usd"]/ACCOUNT_SIZE_USD*100,1)
    r.update({"verdict":"TAKE_IT","np":m["np_usd"],"np_rm":m["np_rm"],
              "nl":m["nl_usd"],"nl_rm":m["nl_rm"],"fees":m["fees"],
              "pop":m["pop"],"be":round(short_strike-credit,2),"risk_pct":risk_pct})
    print(f"  TAKE IT | ${credit} | Loss:${m['nl_usd']} | PoP:{m['pop']}%")
    return r

# ── MESSAGES ──────────────────────────────────────────────
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
            f"IVR: {r.get('ivr',0):.0f} | IV:{r.get('iv','?')}%\n\n"
            f"ECONOMICS (after fees)\n"
            f"Credit:     ${r['credit']:.2f}\n"
            f"Max profit: ${r['np']:.2f} / RM{r['np_rm']:.0f}\n"
            f"Max loss:   ${r['nl']:.2f} / RM{r['nl_rm']:.0f}\n"
            f"Break-even: ${r['be']:.2f}\n"
            f"PoP:        {r['pop']}%\nFees: ${r['fees']:.2f}\n\n"
            f"1 contract | {r['risk_pct']}% risk\n{warn}\n"
            f"Earnings: {r['earnings']}\n\n"
            f"MANAGEMENT\n"
            f"Profit: ${r['credit']/2:.2f} debit (50%)\n"
            f"Stop:   ${r['credit']*2:.2f} debit (2x)\n"
            f"Close by 21DTE\n"
            f"Exit if below ${r['short_strike']}\n\n"
            f"Open IBKR to verify and trade")

def fmt_skip(r):
    return f"SKIP: {r['ticker']}\nReason: {r['reason']}\nIVR:{r.get('ivr',0):.0f} | {r.get('earnings','?')}"

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

# ── MAIN ──────────────────────────────────────────────────
def run():
    now=datetime.datetime.utcnow()
    print("="*50)
    print("BILLY OPTIONS SCANNER - CLOUD")
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC = 9:30 PM MYT")
    print(f"Watchlist: {len(WATCHLIST)} tickers")
    print("="*50)
    send_telegram(f"Billy Scanner Starting\n{now.strftime('%Y-%m-%d %H:%M')} UTC (9:30 PM MYT)\nAccount: ${ACCOUNT_SIZE_USD:,} USD\nScanning {len(WATCHLIST)} tickers:\n{', '.join(WATCHLIST)}")
    vix=get_vix(); mkt=get_market()
    spy=mkt.get("SPY",{})
    print(f"VIX:{vix} | SPY:${spy.get('price','?')}")
    send_telegram(fmt_market(vix,mkt))
    if vix and vix>30:
        send_telegram(f"VIX ALERT: {vix}\nVIX > 30 = High Fear\nStand aside today.")
        return
    results=[]
    for i,ticker in enumerate(WATCHLIST,1):
        try:
            print(f"\n[{i}/{len(WATCHLIST)}] {ticker}")
            r=scan_ticker(ticker); results.append(r)
            send_telegram(fmt_trade(r) if r["verdict"]=="TAKE_IT" else fmt_skip(r))
            time.sleep(2)
        except Exception as e:
            print(f"  Error {ticker}: {e}"); continue
    send_telegram(fmt_summary(results,vix))
    takes=[r["ticker"] for r in results if r["verdict"]=="TAKE_IT"]
    print(f"\nDONE | Scanned:{len(results)} | Trades:{takes or 'None'}")

if __name__=="__main__":
    run()
