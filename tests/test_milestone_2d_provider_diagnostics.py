from unittest.mock import MagicMock, mock_open, patch
import pytest

def make_r(ok=True):
    r = MagicMock()
    r.to_dict.return_value = {"provider":"AlphaVantage","data_type":"price","symbol":"AAPL","ok":ok,"quality":"VERIFIED","value":{"price":150.0},"raw":{"05. price":"150.0"},"source_label":"AlphaVantage","fetched_at_utc":"2026-05-29T05:00:00Z","stale":False,"staleness_reason":"","error":""}
    return r

def test_collect_appends_metadata_fields():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    s._collect_provider_result(make_r())
    assert len(s._provider_diagnostics) == 1
    e = s._provider_diagnostics[0]
    assert e["provider"] == "AlphaVantage"
    assert e["ok"] is True
    assert e["quality"] == "VERIFIED"
    assert "fetched_at_utc" in e

def test_collect_excludes_value_and_raw():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    s._collect_provider_result(make_r())
    e = s._provider_diagnostics[0]
    assert "value" not in e
    assert "raw" not in e

def test_collect_never_raises_on_bad_input():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    s._collect_provider_result(None)
    s._collect_provider_result("bad")
    s._collect_provider_result(42)
    assert isinstance(s._provider_diagnostics, list)

def test_reset_clears_collector():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    s._collect_provider_result(make_r())
    s._collect_provider_result(make_r())
    assert len(s._provider_diagnostics) == 2
    s.reset_provider_diagnostics()
    assert len(s._provider_diagnostics) == 0

def test_write_returns_true_on_success():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    with patch("builtins.open", mock_open()), patch("json.dump"):
        assert s.write_provider_diagnostics() is True

def test_write_returns_false_on_failure():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    with patch("builtins.open", side_effect=IOError("disk full")):
        assert s.write_provider_diagnostics() is False

def test_write_does_not_contain_value_or_raw():
    import billy_options_scanner as s
    s.reset_provider_diagnostics()
    s._collect_provider_result(make_r())
    written = {}
    def capture(data, f, **kw): written.update(data)
    with patch("builtins.open", mock_open()), patch("json.dump", side_effect=capture):
        s.write_provider_diagnostics()
    for entry in written.get("results", []):
        assert "value" not in entry
        assert "raw" not in entry

def test_run_calls_write_provider_diagnostics():
    # Verify write_provider_diagnostics is called after write_journal in run().
    from contextlib import ExitStack
    from types import SimpleNamespace
    from unittest.mock import patch
    import billy_options_scanner as s

    fake_result = {
        "ticker": "SPY",
        "verdict": "SKIP",
        "reason": "mocked",
        "category": "ETF",
    }

    with ExitStack() as stack:
        stack.enter_context(patch("billy_options_scanner.WATCHLIST", ["SPY"]))
        stack.enter_context(patch("billy_options_scanner._ensure_fresh_health_report"))
        mock_vix = stack.enter_context(patch("provider_wrappers.wrap_vix"))
        stack.enter_context(patch("billy_options_scanner.get_market", return_value="mock-market"))
        stack.enter_context(patch("billy_options_scanner.vix_label", return_value="Neutral"))
        stack.enter_context(patch("billy_options_scanner.check_market_trend", return_value=("BULLISH", "mock trend")))
        stack.enter_context(patch("billy_options_scanner.fmt_market", return_value="market msg"))
        stack.enter_context(patch("billy_options_scanner.fmt_skip", return_value="skip msg"))
        stack.enter_context(patch("billy_options_scanner.fmt_summary", return_value="summary msg"))
        stack.enter_context(patch("billy_options_scanner.send_telegram"))
        stack.enter_context(patch("billy_options_scanner.scan_ticker", return_value=fake_result))
        mock_journal = stack.enter_context(patch("billy_options_scanner.write_journal"))
        mock_diagnostics = stack.enter_context(patch("billy_options_scanner.write_provider_diagnostics"))
        stack.enter_context(patch("billy_options_scanner.reset_provider_diagnostics"))
        stack.enter_context(patch("billy_options_scanner._finalize_health_report_after_scan"))
        stack.enter_context(patch("billy_options_scanner.time.sleep"))

        mock_vix.return_value = SimpleNamespace(ok=True, value=15.0)

        s.run()

    mock_journal.assert_called_once()
    mock_diagnostics.assert_called_once()
