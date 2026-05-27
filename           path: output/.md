# Billy Options Scanner

> **IMPORTANT: This scanner is for educational and personal screening only.**
> It does NOT place trades and does NOT constitute financial advice.
> All signals must be verified manually in your broker before acting.

## What it does

Billy Options Scanner is a Python bot that runs on GitHub Actions and scans a
watchlist of tickers for potential **bull put spread** setups using the
tastytrade / Tom Sosnoff framework. It sends Telegram alerts with setup details.

The scanner is designed to be **conservative**. It will never return a confident
`TAKE_IT` signal when important data is missing, estimated, or risky. When data
quality is uncertain, it returns `MANUAL_CHECK` instead.

## Strategy: Bull Put Spread

A bull put spread is a defined-risk options strategy:

- **Sell** an OTM put at ~0.30 delta (short strike)
- **Buy** a lower put 5 points below (long strike, protection)
- Collect a net credit
- Profit if the underlying stays above the short strike at expiry
- Maximum loss is capped at `(spread width - credit) x 100`

Entry criteria (tastytrade framework):

- IVR >= 30 (ideally >= 50)
- 25-52 DTE (target ~45 DTE)
- Credit >= 1/3 of spread width
- Delta <= 0.35 on short leg
- Open interest >= 50 on short strike
- Bid/ask spread <= $0.50
- No earnings within 14 days
- Risk <= 2% of account ($500 on $25k account)

## Verdicts

| Verdict | Meaning |
|---|---|
| `TAKE_IT` | All required live/verified data passed. Possible setup found. |
| `MANUAL_CHECK` | Possible setup but data quality is weak or a soft rule triggered. |
| `SKIP` | Failed a hard rule (IVR, earnings, risk, trend, liquidity). |

**`TAKE_IT` requires ALL of the following to be true:**

- Both short and long leg prices verified (not estimated)
- Credit is calculated from live bid/ask, not estimated
- IVR confirmed from Barchart (not yfinance estimate)
- Earnings date confirmed (not unknown)
- Delta is known (not a fixed-OTM proxy)
- Risk <= 2% of account
- Ticker is above its 50-day moving average
- Market trend: SPY or QQQ above their 50-day moving average
- High-risk stocks: IVR >= 50 and earnings confirmed

## Ticker Classification

| Class | Tickers | Notes |
|---|---|---|
| ETF | SPY, QQQ, IWM, GLD, TLT, XLE, XLF, ... | Preferred. No earnings risk. |
| Normal stocks | AAPL, AMD, META, AMZN | Standard rules apply. |
| High-risk stocks | TSLA, NVDA, COIN, MSTR, PLTR | Require IVR >= 50 + confirmed earnings. |

## Portfolio Exposure Limits

- Maximum **2 TAKE_IT** signals per scan run
- Maximum **1 TAKE_IT** for high-risk stocks per run
- Extra valid trades are downgraded to `MANUAL_CHECK` with reason: "Trade limit reached"
- These limits exist to prevent overexposure in a single scan session

## Data Sources

| Source | Used for | Notes |
|---|---|---|
| Alpha Vantage (AV_API_KEY) | Price, options data (non-ETFs) | Free tier: 25 calls/day |
| yfinance | HV, IVR approx, options chain, VIX, earnings | Free, no key needed |
| Barchart (scrape) | IVR (most reliable free source) | Web scrape, may break |

## Required GitHub Secrets

Set these in your repo: **Settings -> Secrets and variables -> Actions**

| Secret | Description |
|---|---|
| `TELEGRAM_TOKEN` | Your Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat/channel ID |
| `AV_API_KEY` | Alpha Vantage API key (free at alphavantage.co) |

## Schedule

The scanner runs automatically at **1:30 PM UTC (9:30 PM MYT)** on weekdays.
This is after US market close to catch end-of-day data.

Cron: `30 13 * * 1-5`

## How to Run Manually

1. Go to the **Actions** tab in your GitHub repo
2. Click **Billy Options Scanner** in the left sidebar
3. Click **Run workflow**
4. Click the green **Run workflow** button

Or run locally:

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
export AV_API_KEY=your_av_key
python billy_options_scanner.py
```

## Telegram Alert Format

Each alert shows:

- Verdict: TAKE_IT / MANUAL_CHECK / SKIP
- Data quality: VERIFIED / ESTIMATED / MISSING
- Reason (if not TAKE_IT)
- Ticker, expiry, short strike, long strike
- Credit, max profit, max loss, risk %
- IVR source, option data source
- Earnings status (CONFIRMED / UNKNOWN / ETF)
- Trend status (BULLISH / CAUTION / BEARISH)
- Reminder to verify in broker before placing

## Trend Filter

The scanner checks 20/50/200-day moving averages for SPY, QQQ, and each individual ticker.

- If both SPY and QQQ are below their 50-day MA: no new `TAKE_IT` signals
- If individual ticker is below its 50-day MA: `MANUAL_CHECK` or `SKIP`
- If individual ticker is below its 200-day MA: `MANUAL_CHECK`

## Limitations

- **Data is not real-time.** Alpha Vantage free tier data may be delayed.
- **IVR from Barchart is scraped** and may fail if their page structure changes.
- **Option prices from yfinance** may not reflect live bid/ask in your broker.
- **Delta approximation** is used for ETFs and as a fallback; verify live Greeks.
- **Earnings dates** from yfinance can be wrong or missing; always verify.
- **The scanner cannot see your open positions.** Portfolio limits are per-scan only.
- **No broker integration.** Cannot execute trades, check margin, or verify fills.
- **Free API limits.** Alpha Vantage free tier is 25 calls/day. ETFs use yfinance.

## Risk Warning

**Options trading involves significant risk and is not suitable for everyone.**

- You can lose the full amount invested in any options position
- Bull put spreads have defined but real maximum loss
- Past performance of any strategy does not guarantee future results
- This scanner does not account for gap risk, liquidity risk, or assignment risk
- Never risk money you cannot afford to lose

## Why You Must Verify Manually

This scanner uses free data sources with limitations. Before placing any trade:

1. **Confirm IVR** in your broker or Barchart
2. **Confirm delta** using live broker Greeks (not approximations)
3. **Confirm earnings** date is more than 14 days away
4. **Check bid/ask** live in your broker for realistic fill prices
5. **Check open interest** live in your broker
6. **Calculate your actual risk** based on live fill prices
7. **Check market conditions** at time of order placement

---

*This project is for personal screening only. Not financial advice.*
