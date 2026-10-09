"""Phase 2B-1 staging contracts, preserving economically distinct rows."""
from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from portfolio_analytics.input_engine.batch import ingest_files, apply_account_assignments


def csv(rows):
    return pd.DataFrame(rows).to_csv(index=False).encode()


def parts(batch):
    return [p for f in batch["files"] for p in f["parts"]]


def test_maya_three_accounts_repeated_security_preserved_with_provenance():
    uploads = [("isa.csv", csv({"Account": ["ISA"], "Ticker": ["NVDA"], "Quantity": [10], "Current Price": [100]})),
               ("pension.csv", csv({"Account ID": ["Pension"], "Symbol": ["NVDA"], "Shares": [20], "Price": [100]})),
               ("gia.csv", csv({"Portfolio": ["GIA"], "Ticker": ["FUND"], "Quantity": [5], "Current Price": [10]}))]
    batch = ingest_files(uploads, session_id="test-session", imported_at="2026-10-09T12:00:00Z")
    assert batch["summary"] == {"files": 3, "accounts": ["GIA", "ISA", "Pension"], "unresolved_assignments": 0, "blocked_files": 0, "holdings": 3, "transactions": 0, "cashflows": 0}
    nvda = [r for p in parts(batch) for r in p["parsed"]["normalised_dataset"]["holdings"] if r["Ticker"] == "NVDA"]
    assert [r["Quantity"] for r in nvda] == [10, 20]
    assert {r["_provenance"]["account_id"] for r in nvda} == {"ISA", "Pension"}
    assert batch["canonical_acceptance"] == "not_available_in_2B_1"
    assert all(p["parsed"]["inputs"]["starting_cash"] == 0 for p in parts(batch))


def test_missing_account_not_inferred_from_ticker_or_filename_and_correctable():
    batch = ingest_files([("ISA.csv", csv({"Ticker": ["NVDA"], "Quantity": [10]}))])
    before = deepcopy(batch)
    part = parts(batch)[0]
    assert part["account_id"] is None and batch["status"] == "review_required"
    fixed = apply_account_assignments(batch, {part["part_id"]: "ISA"})
    assert batch == before
    p = parts(fixed)[0]
    assert p["original_account_id"] is None and p["account_assignment"] == "user_corrected"
    assert p["parsed"]["normalised_dataset"]["holdings"][0]["_provenance"]["account_id"] == "ISA"
    assert fixed["files"][0]["raw_records"][0]["_provenance"]["account_id"] == "ISA"
    assert fixed["summary"]["unresolved_assignments"] == 0


def test_multi_account_file_and_leading_zero_ids_are_not_collapsed():
    batch = ingest_files([("accounts.csv", b"Account ID,Ticker,Quantity\n0007,A,1\n7,A,2\n,A,3\n")])
    assert batch["summary"]["accounts"] == ["0007", "7"]
    assert batch["summary"]["unresolved_assignments"] == 1
    assert [p["source_rows"] for p in parts(batch)] == [[2], [3], [4]]


def test_literal_na_account_identifiers_are_preserved_as_identifiers():
    batch = ingest_files([("accounts.csv", b"Account,Ticker,Quantity\nNA,A,1\nNULL,A,2\n,A,3\n")])
    assert batch["summary"]["accounts"] == ["NA", "NULL"]
    assert batch["summary"]["unresolved_assignments"] == 1


def test_identical_files_and_transactions_across_accounts_retained_for_later_review():
    raw = csv({"Date": ["2026-01-01"], "Type": ["BUY"], "Ticker": ["A"], "Quantity": [1], "Price": [10], "Transaction ID": ["T1"]})
    first = ingest_files([("one.csv", raw), ("two.csv", raw)])
    second = ingest_files([("one.csv", raw)])
    assert first["files"][0]["fingerprint"] == first["files"][1]["fingerprint"] == second["files"][0]["fingerprint"]
    assert first["files"][0]["source_id"] != first["files"][1]["source_id"] != second["files"][0]["source_id"]
    assignments = {p["part_id"]: name for p, name in zip(parts(first), ["ISA", "GIA"])}
    staged = apply_account_assignments(first, assignments)
    assert staged["summary"]["transactions"] == 2 and staged["duplicate_review"] == "pending"


def test_snapshot_ledger_overlap_same_account_is_preserved_without_accounting_guess():
    batch = ingest_files([
        ("snapshot.csv", csv({"Account": ["ISA"], "Ticker": ["A"], "Quantity": [5]})),
        ("ledger1.csv", csv({"Account": ["ISA"], "Date": ["2026-01-01"], "Type": ["BUY"], "Ticker": ["A"], "Quantity": [5], "Price": [10]})),
        ("ledger2.csv", csv({"Account": ["ISA"], "Date": ["2026-01-01"], "Type": ["BUY"], "Ticker": ["A"], "Quantity": [5], "Price": [10]})),
    ])
    assert [p["parsed"]["classification"] for p in parts(batch)] == ["holdings", "ledger", "ledger"]
    assert batch["summary"]["holdings"] == 1 and batch["summary"]["transactions"] == 2
    assert batch["duplicate_review"] == "pending"  # No claim that this is safe to consolidate.


def test_different_snapshots_are_distinct_sources_even_with_same_account():
    batch = ingest_files([(f"snapshot{i}.csv", csv({"Account": ["ISA"], "Ticker": ["A"], "Quantity": [i]})) for i in [1, 2]])
    assert len(parts(batch)) == 2 and batch["files"][0]["fingerprint"] != batch["files"][1]["fingerprint"]


def test_james_fund_unpriced_asset_short_gbx_and_foreign_cash_keep_input_units():
    batch = ingest_files([("james.csv", csv({"Account": ["GIA"]*4, "Ticker": ["EQUITY", "FUND", "PRIVATE", "CASH"],
        "Quantity": [-2, 3, 1, 100], "Current Price": [100, 500, None, 1], "Currency": ["USD", "GBX", "EUR", "EUR"]}))])
    rows = parts(batch)[0]["parsed"]["normalised_dataset"]["holdings"]
    assert [r["Quantity"] for r in rows] == [-2, 3, 1, 100]
    assert [r["Currency"] for r in rows] == ["USD", "GBX", "EUR", "EUR"]
    assert pd.isna(rows[2]["Current Price"]) and rows[3]["Ticker"] == "CASH"
    assert not parts(batch)[0]["parsed"]["variables"]["history_capability"]["available"]


def test_xlsx_uses_existing_first_sheet_parser_and_retains_provenance():
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({"Account": ["Pension"], "Ticker": ["A"], "Quantity": [2]}).to_excel(writer, index=False)
    batch = ingest_files([("pension.xlsx", output.getvalue())])
    assert batch["summary"]["holdings"] == 1
    assert parts(batch)[0]["parsed"]["source"]["provenance"]["source_type"] == "xlsx"


@pytest.mark.parametrize("name,data", [("empty.csv", b""), ("headers.csv", b"Ticker,Quantity\n"), ("unknown.txt", b"x")])
def test_bad_files_do_not_remove_other_sources(name, data):
    batch = ingest_files([(name, data), ("good.csv", b"Account,Ticker,Quantity\nISA,A,1\n")])
    assert batch["summary"]["blocked_files"] == 1 and batch["summary"]["holdings"] == 1
    assert batch["files"][0]["issues"] and batch["status"] == "review_required"


def test_partial_file_retains_unsupported_raw_records_and_blocks_file():
    raw = b"Account,Date,Type,Ticker,Quantity,Price\nISA,2026-01-01,BUY,A,1,10\nISA,2026-01-02,TRANSFER,A,1,10\n"
    batch = ingest_files([("partial.csv", raw)])
    assert batch["summary"]["blocked_files"] == 1
    assert batch["summary"]["transactions"] == 1
    assert len(batch["files"][0]["raw_records"]) == 2
    assert any("Unsupported ledger event" in i["issue"] for i in batch["files"][0]["issues"])


def test_conflicting_account_columns_require_source_correction():
    batch = ingest_files([("conflict.csv", b"Account,Portfolio,Ticker,Quantity\nISA,GIA,A,1\n")])
    assert batch["summary"]["blocked_files"] == 1
    assert len(batch["files"][0]["raw_records"]) == 1
    assert "authoritative account column" in batch["files"][0]["issues"][0]["issue"]


def test_realistic_large_import_preserves_all_rows_and_accounts():
    rows = pd.DataFrame({"Account": [f"ACCOUNT-{i%3}" for i in range(6000)], "Ticker": [f"ASSET{i%100}" for i in range(6000)], "Quantity": 1})
    batch = ingest_files([("large.csv", rows.to_csv(index=False).encode())])
    assert batch["summary"]["holdings"] == 6000 and len(batch["summary"]["accounts"]) == 3
    assert sum(len(p["source_rows"]) for p in parts(batch)) == 6000


def test_unknown_correction_and_empty_batch_are_explicit():
    assert ingest_files([])["status"] == "empty"
    with pytest.raises(ValueError, match="unknown import part"):
        apply_account_assignments(ingest_files([]), {"unknown": "ISA"})
