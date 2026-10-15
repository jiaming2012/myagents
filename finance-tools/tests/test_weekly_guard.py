"""The weekly guard turns a daily launchd trigger into an at-most-once-per-6-days send."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weekly_health import weekly_guard_record_sent, weekly_guard_should_send  # noqa: E402


def test_guard_sends_when_never_sent(tmp_path):
    f = tmp_path / "health_last_sent"
    assert weekly_guard_should_send(last_sent_file=f) is True


def test_guard_blocks_within_window_and_allows_after(tmp_path):
    f = tmp_path / "health_last_sent"
    monday = datetime(2026, 10, 19, 8, 0)
    weekly_guard_record_sent(now=monday, last_sent_file=f)
    assert f.read_text() == "2026-10-19T08:00:00"
    # Tue..Sat: no resend
    for d in range(1, 6):
        assert weekly_guard_should_send(now=monday + timedelta(days=d), last_sent_file=f) is False
    # Sunday (6 days) and later: send
    assert weekly_guard_should_send(now=monday + timedelta(days=6), last_sent_file=f) is True
    assert weekly_guard_should_send(now=monday + timedelta(days=9), last_sent_file=f) is True


def test_guard_treats_garbage_as_never_sent(tmp_path):
    f = tmp_path / "health_last_sent"
    f.write_text("not a date")
    assert weekly_guard_should_send(last_sent_file=f) is True


def test_power_off_monday_catches_up_tuesday(tmp_path):
    """Mac off Monday 08:00 -> daily trigger fires Tuesday -> guard allows the send."""
    f = tmp_path / "health_last_sent"
    weekly_guard_record_sent(now=datetime(2026, 10, 12, 8, 0), last_sent_file=f)   # previous Monday
    tuesday = datetime(2026, 10, 20, 8, 0)
    assert weekly_guard_should_send(now=tuesday, last_sent_file=f) is True
    weekly_guard_record_sent(now=tuesday, last_sent_file=f)
    # the following Monday is only 6 days later -> still sends, cadence snaps back
    assert weekly_guard_should_send(now=datetime(2026, 10, 26, 8, 0), last_sent_file=f) is True
