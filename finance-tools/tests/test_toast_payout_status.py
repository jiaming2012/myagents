"""toast_payout_status: provisional payout rows are detected and refreshed safely."""
import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

import toast_payout_status as tps

HEADER = ["Settled date", "Name", "Location", "Type", "Sales period start", "Sales period end", "# Txns",
          "Payments", "Refunds", "Fees", "Withholdings", "Chargebacks", "External", "Payout", "External Ref. ID", "Status"]


def _row(settled, payments, fees, payout, status, ref="X"):
    return [settled, "YumYums", "Forrestville Plaza", "PAYOUT", "", "", "9", payments, "0.0", fees,
            "0.0", "0.0", "0.0", payout, ref, status]


def write_zip(path: Path, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("PayoutOverviewByPayout.csv", buf.getvalue())


@pytest.fixture
def inputs(tmp_path):
    write_zip(tmp_path / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip", [
        _row("11/16/2026", "307.59", "11.77", "331.55", "CAPTURED"),
        _row("11/15/2026", "430.53", "16.83", "413.7", "SENT"),
    ])
    write_zip(tmp_path / "PayoutOverviewByPayout_2026-09-28_2026-10-15.zip", [
        _row("10/15/2026", "288.33", "11.28", "277.05", "SENT"),
    ])
    (tmp_path / "SalesSummary_2026-11-14_2026-11-14.zip").write_bytes(b"not a payout file")
    return tmp_path


def test_range_and_status_index(inputs):
    assert tps.payout_range(inputs / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip") == (date(2026, 10, 16), date(2026, 11, 16))
    assert tps.payout_range(inputs / "SalesSummary_2026-11-14_2026-11-14.zip") is None
    idx = tps.status_index(inputs)
    assert idx[("2026-11-16", "331.55")] == "CAPTURED"
    assert idx[("2026-11-15", "413.70")] == "SENT"
    assert idx[("2026-10-15", "277.05")] == "SENT"


def test_refresh_replaces_the_stale_file_when_the_export_succeeds(inputs):
    stale = inputs / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip"
    calls = []

    def fake_export(frm, to):
        calls.append((frm, to))
        assert not stale.exists(), "the stale zip must be out of the way so the exporter downloads again"
        write_zip(stale, [_row("11/16/2026", "345.04", "13.49", "331.55", "SENT"),
                          _row("11/15/2026", "430.53", "16.83", "413.7", "SENT")])
        return 0

    said = []
    done = tps.refresh_provisional(inputs, say=said.append, run=fake_export)
    assert calls == [(date(2026, 10, 16), date(2026, 11, 16))]
    assert done == [("PayoutOverviewByPayout_2026-10-16_2026-11-16.zip", 1, 0)]
    assert tps.status_index(inputs)[("2026-11-16", "331.55")] == "SENT"
    assert not (inputs / tps.PROVISIONAL_SUBDIR / stale.name).exists()
    assert any("re-exporting 2026-10-16 → 2026-11-16" in m for m in said)


def test_refresh_restores_the_old_file_when_the_export_fails(inputs):
    stale = inputs / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip"
    before = stale.read_bytes()
    said = []
    done = tps.refresh_provisional(inputs, say=said.append, run=lambda f, t: 3)
    assert done == [("PayoutOverviewByPayout_2026-10-16_2026-11-16.zip", 1, None)]
    assert stale.read_bytes() == before
    assert not (inputs / tps.PROVISIONAL_SUBDIR / stale.name).exists()
    assert any("kept the old" in m for m in said)


def test_refresh_restores_when_the_exporter_raises(inputs):
    stale = inputs / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip"

    def boom(f, t):
        raise FileNotFoundError("task")

    tps.refresh_provisional(inputs, say=lambda m: None, run=boom)
    assert stale.exists()


def test_nothing_to_refresh_when_every_row_is_sent(tmp_path):
    write_zip(tmp_path / "PayoutOverviewByPayout_2026-10-16_2026-11-16.zip",
              [_row("11/16/2026", "345.04", "13.49", "331.55", "SENT")])
    assert tps.refresh_provisional(tmp_path, say=lambda m: None, run=lambda f, t: pytest.fail("no export expected")) == []
