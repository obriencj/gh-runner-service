from datetime import datetime, timedelta, timezone

import pytest

from preoccupied.gh_runner_ctl import CtlError
from preoccupied.gh_runner_ctl import prune


class TestDuration:
    @pytest.mark.parametrize(
        "text,seconds",
        [("30s", 30), ("30m", 1800), ("2h", 7200), ("14d", 1209600), ("1w", 604800)],
    )
    def test_parse(self, text, seconds):
        assert prune.parse_duration(text) == seconds

    @pytest.mark.parametrize("text", ["", "30", "2 hours", "-1h", "2y"])
    def test_reject(self, text):
        with pytest.raises(CtlError):
            prune.parse_duration(text)


class TestAge:
    def test_recent(self):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        assert 290 < prune._age_seconds(stamp) < 310

    def test_podman_nanosecond_precision(self):
        """Podman emits more fractional digits than fromisoformat accepts."""
        stamp = "2026-07-29T10:00:00.123456789Z"
        assert prune._age_seconds(stamp) is not None

    def test_zero_value_is_unknown(self):
        assert prune._age_seconds("0001-01-01T00:00:00Z") is None

    def test_garbage_is_unknown(self):
        assert prune._age_seconds("not a date") is None
        assert prune._age_seconds("") is None
