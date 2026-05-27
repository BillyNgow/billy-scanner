# Billy Options Scanner

Educational and personal screening tool for bull put spread candidates.

This scanner does not place trades. All trade ideas must be verified manually in your broker before acting.

## Schedule

The scanner runs automatically at **21:30 UTC (05:30 AM MYT next day)** on weekdays.

Cron:

```text
30 21 * * 1-5
```

This is well after the US market close and is intended to catch settled end-of-day data.

Before the scan, GitHub Actions runs:

```bash
python billy_options_scanner.py validate-config
```

This performs one Alpha Vantage health probe and writes:

```text
output/health_report_YYYY-MM-DD.json
```

Then the scanner runs:

```bash
python billy_options_scanner.py scan
```

The scan reuses the health report if `probed_at_utc` is less than 10 minutes old. This prevents wasting an extra Alpha Vantage API call during the same workflow run.

Alpha Vantage quota accounting is:

```text
AV total = AV_PRE_PROBE_CALLS + AV_CALL_COUNT
```

The `validate-config` workflow step uses:

```yaml
continue-on-error: true
```

So if Alpha Vantage is missing, invalid, or rate-limited, the workflow can still continue in conservative fallback mode.

Alpha Vantage improves data-source checking, but it is **not broker-grade execution data**. Any trade idea must still be verified manually in your broker before acting.