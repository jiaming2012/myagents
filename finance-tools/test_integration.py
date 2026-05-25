"""
Integration test: verifies the Zoho Calendar payments script returns > 0 events.

Requires valid credentials in .env or environment variables.
Run: task test
"""

import re
import subprocess
import sys


def test_payments_month_returns_events():
    result = subprocess.run(
        [sys.executable, "zoho_calendar_payments.py", "--days", "30"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    match = re.search(r"Total: (\d+) event\(s\)", result.stdout)
    assert match, f"Could not find 'Total: N event(s)' in output:\n{result.stdout}"

    count = int(match.group(1))
    assert count > 0, f"Expected > 0 events, got {count}\n{result.stdout}"


if __name__ == "__main__":
    test_payments_month_returns_events()
    print("PASSED: payments:month returned > 0 events")
