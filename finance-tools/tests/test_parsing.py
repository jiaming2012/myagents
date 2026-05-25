"""Tests for calendar event parsing functions in zoho_calendar_payments.py."""

import pytest

from zoho_calendar_payments import (
    parse_event_title,
    parse_event_notes,
    build_nickname_lookup,
    resolve_account,
    process_events,
)


# =============================================================
# parse_event_title
# =============================================================


class TestParseEventTitle:
    def test_simple_title(self):
        result = parse_event_title("Quickbooks - $38")
        assert result == {"name": "Quickbooks", "amount": 38.0}

    def test_title_with_commas(self):
        result = parse_event_title("Rent - $2,150.50")
        assert result == {"name": "Rent", "amount": 2150.5}

    def test_title_with_cents(self):
        result = parse_event_title("Spotify - $12.78")
        assert result == {"name": "Spotify", "amount": 12.78}

    def test_invalid_title_no_dash(self):
        with pytest.raises(ValueError, match="does not match"):
            parse_event_title("Bad Title")

    def test_invalid_title_no_amount(self):
        with pytest.raises(ValueError, match="does not match"):
            parse_event_title("Name - noamount")

    def test_title_with_extra_spaces(self):
        result = parse_event_title("  Rent   -  $1,000.00  ")
        assert result == {"name": "Rent", "amount": 1000.0}

    def test_zero_amount(self):
        result = parse_event_title("Name - $0")
        assert result == {"name": "Name", "amount": 0.0}

    def test_title_without_dollar_sign(self):
        result = parse_event_title("Test - 100")
        assert result == {"name": "Test", "amount": 100.0}

    def test_title_large_amount(self):
        result = parse_event_title("Mortgage - $3,500.00")
        assert result == {"name": "Mortgage", "amount": 3500.0}


# =============================================================
# parse_event_notes
# =============================================================


class TestParseEventNotes:
    def test_full_notes(self):
        result = parse_event_notes("Fund: Chase 7667 | Source: Amex | VARIABLE")
        assert result == {
            "fund_account": "Chase 7667",
            "source_account": "Amex",
            "is_variable": True,
            "no_funding": False,
        }

    def test_fund_only(self):
        result = parse_event_notes("Fund: Mercury Personal")
        assert result == {
            "fund_account": "Mercury Personal",
            "source_account": None,
            "is_variable": False,
            "no_funding": False,
        }

    def test_empty_notes(self):
        with pytest.raises(ValueError, match="Missing description"):
            parse_event_notes("")

    def test_none_notes(self):
        with pytest.raises(ValueError, match="Missing description"):
            parse_event_notes(None)

    def test_none_keyword(self):
        result = parse_event_notes("NONE")
        assert result == {
            "fund_account": None,
            "source_account": None,
            "is_variable": False,
            "no_funding": True,
        }

    def test_na_keyword(self):
        result = parse_event_notes("N/A")
        assert result == {
            "fund_account": None,
            "source_account": None,
            "is_variable": False,
            "no_funding": True,
        }

    def test_missing_fund_prefix(self):
        with pytest.raises(ValueError, match="No 'Fund:' field found"):
            parse_event_notes("No Fund: prefix here")

    def test_fund_and_variable_no_source(self):
        result = parse_event_notes("Fund: BoA Business | VARIABLE")
        assert result == {
            "fund_account": "BoA Business",
            "source_account": None,
            "is_variable": True,
            "no_funding": False,
        }

    def test_whitespace_only_notes(self):
        with pytest.raises(ValueError, match="Missing description"):
            parse_event_notes("   ")


# =============================================================
# build_nickname_lookup + resolve_account
# =============================================================


SAMPLE_CONFIG = {
    "accounts": [
        {
            "id": "chase-ink-7667",
            "name": "Chase Ink",
            "institution": "Chase",
            "last4": "7667",
            "nicknames": ["Chase 7667", "Ink 7667", "Chase Ink", "Ink"],
        },
        {
            "id": "amex-delta-1008",
            "name": "Delta SkyMiles",
            "institution": "American Express",
            "last4": "1008",
            "nicknames": ["Amex", "Amex Delta", "Amex 1008"],
        },
    ]
}


class TestNicknameLookup:
    def test_build_lookup(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        assert "chase 7667" in lookup
        assert "amex" in lookup
        assert lookup["chase 7667"] == "chase-ink-7667"
        assert lookup["amex"] == "amex-delta-1008"

    def test_resolve_known(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        assert resolve_account("Chase 7667", lookup) == "chase-ink-7667"

    def test_resolve_unknown(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        with pytest.raises(ValueError, match="Unknown account nickname"):
            resolve_account("Unknown Acct", lookup)

    def test_case_insensitive(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        assert resolve_account("CHASE 7667", lookup) == "chase-ink-7667"
        assert resolve_account("amex", lookup) == "amex-delta-1008"
        assert resolve_account("INK", lookup) == "chase-ink-7667"

    def test_lookup_includes_account_id(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        assert resolve_account("chase-ink-7667", lookup) == "chase-ink-7667"

    def test_lookup_includes_display_name(self):
        lookup = build_nickname_lookup(SAMPLE_CONFIG)
        assert resolve_account("Chase Ink", lookup) == "chase-ink-7667"
        assert resolve_account("Delta SkyMiles", lookup) == "amex-delta-1008"


# =============================================================
# process_events (collect-and-report D-03)
# =============================================================


class TestProcessEvents:
    def test_valid_events(self):
        events = [
            {
                "title": "Quickbooks - $38",
                "description": "Fund: Chase 7667",
                "dateandtime": {"start": "20260510T000000Z"},
            },
        ]
        payments, errors = process_events(events, SAMPLE_CONFIG)
        assert len(payments) == 1
        assert len(errors) == 0
        assert payments[0]["name"] == "Quickbooks"
        assert payments[0]["amount"] == 38.0
        assert payments[0]["fund_account"] == "Chase 7667"
        assert payments[0]["fund_account_id"] == "chase-ink-7667"

    def test_mixed_valid_invalid(self):
        events = [
            {
                "title": "Quickbooks - $38",
                "description": "Fund: Chase 7667",
                "dateandtime": {"start": "20260510T000000Z"},
            },
            {
                "title": "Bad Title No Amount",
                "description": "Fund: Chase 7667",
                "dateandtime": {"start": "20260511T000000Z"},
            },
            {
                "title": "Rent - $1,000",
                "description": "",
                "dateandtime": {"start": "20260501T000000Z"},
            },
        ]
        payments, errors = process_events(events, SAMPLE_CONFIG)
        assert len(payments) == 1
        assert len(errors) == 2

    def test_all_invalid(self):
        events = [
            {
                "title": "Bad",
                "description": "Fund: Chase 7667",
                "dateandtime": {"start": "20260510T000000Z"},
            },
            {
                "title": "Also Bad",
                "description": "Fund: Chase 7667",
                "dateandtime": {"start": "20260511T000000Z"},
            },
        ]
        payments, errors = process_events(events, SAMPLE_CONFIG)
        assert len(payments) == 0
        assert len(errors) == 2

    def test_no_funding_event(self):
        """Events with NONE notes should process without error."""
        events = [
            {
                "title": "Info Event - $0",
                "description": "NONE",
                "dateandtime": {"start": "20260510T000000Z"},
            },
        ]
        payments, errors = process_events(events, SAMPLE_CONFIG)
        assert len(payments) == 1
        assert len(errors) == 0
        assert payments[0]["no_funding"] is True
        assert payments[0]["fund_account_id"] is None
