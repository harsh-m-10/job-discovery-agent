"""Referral matching tests. Synthetic data only — no real export is read.

The expensive failure here is a false positive: a confident-looking match sends
the operator to message a stranger about a job at a company they never worked
for. Several tests exist purely to pin that down.

    python tests/test_referral.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score.referral import (Contact, match_company, normalize_company,
                            rank_matches)
from scripts.import_connections import (UnsafePath, parse_connected_on,
                                        read_rows, resolve_csv_path, to_record)

ROOT = Path(__file__).resolve().parents[1]

SYNTHETIC_CSV = '''Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Asha,Rao,https://www.linkedin.com/in/asha,,Walmart Global Tech India,Software Engineer II,11 Aug 2026
Vikram,Singh,https://www.linkedin.com/in/vikram,,"Razorpay Software Private Limited",Backend Engineer,02 Jan 2025
Neha,Gupta,https://www.linkedin.com/in/neha,,,,07 Aug 2026
Rahul,Nair,https://www.linkedin.com/in/rahul,,Zeta Suite (Zeta India),Product Manager,15 Mar 2024
'''


# --- normalization ------------------------------------------------------

def test_strips_legal_and_geographic_noise():
    assert normalize_company("Walmart Global Tech India") == "walmart"
    assert normalize_company("Razorpay Software Private Limited") == "razorpay"
    assert normalize_company("Meesho Technologies Pvt Ltd") == "meesho"
    assert normalize_company("Grafana Labs, Inc.") == "grafana"
    assert normalize_company("Cisco Systems India Pvt. Ltd.") == "cisco"


def test_nested_noise_stripped_repeatedly():
    # "technologies india private limited" needs more than one pass.
    assert normalize_company("Postman Technologies India Private Limited") == "postman"


def test_case_punctuation_and_accents():
    assert normalize_company("  ZÉTA   SUITE!!  ") == "zeta suite"
    assert normalize_company("MongoDB, Inc.") == "mongodb"


def test_empty_and_none():
    assert normalize_company(None) == ""
    assert normalize_company("") == ""
    assert normalize_company("   ") == ""


def test_parenthetical_dropped_only_when_something_remains():
    assert normalize_company("Zeta Suite (Zeta India)") == "zeta suite"
    # If the parenthetical is the whole name, keep it rather than return empty.
    assert normalize_company("(Stealth)") != ""


# --- matching -----------------------------------------------------------

def contact(cid: int, company: str, title: str = "Software Engineer",
            batch: bool = False, when: date | None = None) -> Contact:
    return Contact(
        id=cid, full_name=f"Person {cid}", company_raw=company,
        company_norm=normalize_company(company), title=title,
        connected_on=when or date(2025, 1, 1), is_batchmate=batch,
    )


def test_exact_match_after_normalization():
    people = [contact(1, "Walmart Global Tech India"), contact(2, "Stripe")]
    matches = match_company("Walmart", people)
    assert [m.contact.id for m in matches] == [1]
    assert matches[0].method == "exact"


def test_different_companies_sharing_a_prefix_do_not_match():
    # The expensive false positive.
    people = [contact(1, "Walgreens"), contact(2, "Walmart Labs")]
    assert [m.contact.id for m in match_company("Walmart", people)] == [2]


def test_unrelated_company_never_matches():
    people = [contact(1, "Infosys"), contact(2, "Wipro Technologies")]
    assert match_company("Stripe", people) == []


def test_generic_company_names_are_refused():
    # Normalizing "Tech Solutions Pvt Ltd" leaves nothing identifying.
    people = [contact(1, "Tech Solutions Pvt Ltd")]
    assert match_company("Data Systems Limited", people) == []


def test_contacts_without_a_company_are_skipped():
    people = [contact(1, ""), contact(2, "Stripe")]
    assert [m.contact.id for m in match_company("Stripe", people)] == [2]


# --- ranking ------------------------------------------------------------

def test_batchmate_outranks_everyone():
    people = [
        contact(1, "Stripe", "Staff Engineer", batch=False, when=date(2026, 1, 1)),
        contact(2, "Stripe", "Analyst", batch=True, when=date(2020, 1, 1)),
    ]
    ranked = rank_matches(match_company("Stripe", people))
    assert ranked[0].contact.id == 2


def test_engineering_title_outranks_unrelated_title():
    people = [
        contact(1, "Stripe", "Sales Representative", when=date(2026, 1, 1)),
        contact(2, "Stripe", "Backend Engineer", when=date(2026, 1, 1)),
    ]
    ranked = rank_matches(match_company("Stripe", people), target_role="Backend Engineer")
    assert ranked[0].contact.id == 2


def test_recency_breaks_ties():
    people = [
        contact(1, "Stripe", "Software Engineer", when=date(2021, 1, 1)),
        contact(2, "Stripe", "Software Engineer", when=date(2026, 6, 1)),
    ]
    ranked = rank_matches(match_company("Stripe", people))
    assert ranked[0].contact.id == 2


def test_rank_returns_at_most_three():
    people = [contact(i, "Stripe") for i in range(1, 8)]
    assert len(rank_matches(match_company("Stripe", people))) == 3


# --- CSV parsing --------------------------------------------------------

def write_temp_csv(body: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "Connections.csv"
    tmp.write_text(body, encoding="utf-8")
    return tmp


def test_preamble_is_skipped_and_rows_parse():
    rows = read_rows(write_temp_csv(SYNTHETIC_CSV))
    assert len(rows) == 4
    assert rows[0]["First Name"] == "Asha"


def test_records_normalize_company_and_date():
    rows = read_rows(write_temp_csv(SYNTHETIC_CSV))
    records = [to_record(r) for r in rows]
    assert records[0]["full_name"] == "Asha Rao"
    assert records[0]["company_norm"] == "walmart"
    assert records[0]["connected_on"] == "2026-08-11"
    # A connection with no employer keeps the row but nulls the company.
    assert records[2]["company_raw"] is None and records[2]["company_norm"] is None


def test_bad_file_is_rejected_clearly():
    bad = write_temp_csv("some,unrelated,csv\n1,2,3\n")
    try:
        read_rows(bad)
    except ValueError as exc:
        assert "header" in str(exc).lower()
    else:
        raise AssertionError("a non-LinkedIn CSV should be rejected")


def test_date_formats():
    assert parse_connected_on("11 Aug 2026") == date(2026, 8, 11)
    assert parse_connected_on("2026-08-11") == date(2026, 8, 11)
    assert parse_connected_on("") is None
    assert parse_connected_on("nonsense") is None


# --- the path guard -----------------------------------------------------

def test_path_inside_the_repo_is_refused():
    inside = ROOT / "Connections.csv"
    inside.write_text(SYNTHETIC_CSV, encoding="utf-8")
    try:
        resolve_csv_path(str(inside))
    except UnsafePath as exc:
        assert "REFUSING" in str(exc)
    else:
        raise AssertionError("a PII file inside the repo must be refused")
    finally:
        inside.unlink(missing_ok=True)


def test_path_outside_the_repo_is_allowed():
    outside = write_temp_csv(SYNTHETIC_CSV)
    assert resolve_csv_path(str(outside)) == outside.resolve()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc or 'assertion failed'}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
