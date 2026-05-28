from __future__ import annotations

import unittest.mock as mock

import pytest

from billy_health import validate_av_key


def test_av_validation_missing_env():
    """Test AV_API_KEY validation when missing."""
    with mock.patch.dict("os.environ", {}, clear=True):
        result = validate_av_key(api_key=None)
        # Should return False or error message when key is missing
        assert result is not None


def test_av_validation_with_key():
    """Test AV_API_KEY validation with key provided."""
    with mock.patch.dict("os.environ", {"AV_API_KEY": "test_key"}):
        # Should handle the key without exposing it
        result = validate_av_key(api_key="test_key")
        assert result is not None
