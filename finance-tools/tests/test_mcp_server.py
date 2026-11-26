"""Unit tests for the pure parts of mcp_server.py: the figures and the cache (no network)."""
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server as m  # noqa: E402

TODAY = date(2026, 11, 26)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def acct(id_, balance, payments, severity="ok"):
    return {"id": id_, "current_balance": balance, "severity": severity,
            "payments": [{"name": n, "amount": a, "due_date": datetime(2026, 11, 26) + timedelta(days=d)}
                         for n, a, d in payments]}


def test_sync_error_count():
    assert m.sync_error_count(None) is None
    assert m.sync_error_count({"findings": []}) == 0
    r = {"findings": [{"severity": "error"}, {"severity": "warning"}, {"severity": "error"}]}
    assert m.sync_error_count(r) == 2


def test_days_to_shortfall_first_negative_day_across_accounts():
    f = {"accounts": [
        acct("a", 100.0, [("rent", 50.0, 2), ("car", 80.0, 9)]),      # negative on day 9
        acct("b", 10.0, [("gym", 30.0, 4)]),                          # negative on day 4
        acct("c", 1000.0, [("phone", 20.0, 1)]),
    ]}
    assert m.days_to_shortfall(f, 30, TODAY) == 4


def test_days_to_shortfall_is_the_horizon_when_nothing_goes_negative():
    f = {"accounts": [acct("a", 500.0, [("rent", 50.0, 2)]), acct("b", 0.0, [])]}
    assert m.days_to_shortfall(f, 30, TODAY) == 30
    assert m.days_to_shortfall(f, 7, TODAY) == 7


def test_days_to_shortfall_ignores_unknown_balances_and_needs_one_known():
    unknown = acct("u", 0.0, [("x", 10.0, 1)], severity="unknown")
    assert m.days_to_shortfall({"accounts": [unknown]}, 30, TODAY) is None
    assert m.days_to_shortfall({"accounts": [unknown, acct("a", 5.0, [("y", 10.0, 3)])]}, 30, TODAY) == 3
    assert m.days_to_shortfall(None) is None


def test_days_to_shortfall_today_is_zero_not_negative():
    f = {"accounts": [acct("a", 0.0, [("due now", 1.0, 0)])]}
    assert m.days_to_shortfall(f, 30, TODAY) == 0


def test_figures_shape_and_omission():
    sync = {"checked_at": "2026-11-26T11:00:00+00:00", "findings": [{"severity": "error"}]}
    f = {"accounts": [acct("a", 5.0, [("y", 10.0, 3)])]}
    got = m.figures(sync, f, NOW, forecast_at=NOW - timedelta(minutes=10))
    assert [g["name"] for g in got] == ["sync_errors", "days_to_shortfall"]
    assert got[0] == {"name": "sync_errors", "value": 1, "unit": "connections", "as_of": sync["checked_at"]}
    assert got[1]["value"] == 3 and got[1]["unit"] == "days"
    assert got[1]["as_of"] == (NOW - timedelta(minutes=10)).isoformat()
    assert m.figures(None, None, NOW) == []
    assert [g["name"] for g in m.figures(sync, None, NOW)] == ["sync_errors"]


def test_upcoming_flattens_draws_and_card_charges_soonest_first():
    f = {"accounts": [acct("chk", 100.0, [("rent", 50.0, 5), ("phone", 20.0, 1)])],
         "card_charges": [{"name": "netflix", "amount": 15.0, "due_date": datetime(2026, 11, 28), "card_id": "visa"}]}
    rows = m.upcoming(f)
    assert [r["name"] for r in rows] == ["phone", "netflix", "rent"]
    assert rows[1] == {"name": "netflix", "amount": 15.0, "due_date": "2026-11-28", "funding_account": "visa", "kind": "card charge"}
    assert rows[0]["kind"] == "bank draw" and rows[0]["funding_account"] == "chk"


def test_cache_fresh_rules():
    assert not m.cache_fresh(None, NOW)
    assert not m.cache_fresh({"computed_at": "garbage"}, NOW)
    ok = {"computed_at": (NOW - timedelta(minutes=10)).isoformat(), "error": None}
    assert m.cache_fresh(ok, NOW)
    assert not m.cache_fresh({**ok, "computed_at": (NOW - timedelta(minutes=31)).isoformat()}, NOW)
    failed = {"computed_at": (NOW - timedelta(minutes=10)).isoformat(), "error": "boom"}
    assert not m.cache_fresh(failed, NOW), "a failed attempt is retried after five minutes, not thirty"
    assert m.cache_fresh({**failed, "computed_at": (NOW - timedelta(minutes=4)).isoformat()}, NOW)


def test_cached_stores_values_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_FILE", tmp_path / "cache.json")
    calls = []

    async def compute():
        calls.append(1)
        return {"n": len(calls)}

    async def failing():
        raise RuntimeError("no session")

    first = asyncio.run(m.cached("k", compute, NOW))
    again = asyncio.run(m.cached("k", compute, NOW + timedelta(minutes=5)))
    assert first["value"] == {"n": 1} and again["value"] == {"n": 1} and len(calls) == 1
    later = asyncio.run(m.cached("k", compute, NOW + timedelta(minutes=31)))
    assert later["value"] == {"n": 2}
    err = asyncio.run(m.cached("bad", failing, NOW))
    assert err["value"] is None and err["error"].startswith("RuntimeError: no session")
    assert (tmp_path / "cache.json").exists()


def test_cached_turns_a_hung_source_into_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_FILE", tmp_path / "cache.json")

    async def hangs():
        await asyncio.sleep(10)

    entry = asyncio.run(m.cached("slow", hangs, NOW, timeout=0.05))
    assert entry["value"] is None and entry["error"] == "no answer within 0s"
    assert not m.cache_fresh(entry, NOW + timedelta(minutes=6)), "retried after the error TTL"


@pytest.mark.parametrize("name", ["key_indicators", "sync_status", "payment_forecast", "upcoming_payments"])
def test_tools_are_registered(name):
    tools = {t.name: t for t in asyncio.run(m.server.list_tools())}
    assert name in tools
    read_only = tools[name].annotations and tools[name].annotations.read_only_hint
    assert bool(read_only) == (name != "key_indicators")
