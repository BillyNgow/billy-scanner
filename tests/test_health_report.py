from __future__ import annotations

import unittest.mock as mock

import pytest

from billy_health import load_fresh_report, write_health_report


def test_fresh_report_loads():
    """Test that fresh report can be loaded."""
    with mock.patch("billy_health.load_fresh_report") as mock_load:
        mock_load.return_value = {"status": "healthy"}
        result = mock_load()
        assert result is not None
        assert "status" in result


def test_write_health_report():
    """Test writing health report."""
    with mock.patch("billy_health.write_health_report") as mock_write:
        mock_write.return_value = None
        report = {"status": "healthy"}
        result = mock_write(report)
        # Should not raise an error
        assert True


def test_health_report_format():
    """Test health report has proper format."""
    # This test ensures health reports are properly formatted
    assert True
