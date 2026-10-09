"""Deterministic conflict decisions; no accounting or economic-row deletion."""
from copy import deepcopy

import pandas as pd
import pytest

from portfolio_analytics.input_engine.batch import ingest_files, append_files, apply_account_assignments
from portfolio_analytics.input_engine.review import review_view, decide, reverse_decision, confirm_review


def csv(rows):
    return pd.DataFrame(rows).to_csv(index=False).encode()


def issue(batch, code):
    return next(i for i in review_view(batch)["issues"] if i["code"] == code)


def holdings(account="ISA", quantity=10, date=None):
    rows = {"Account": [account], "Ticker": ["NVDA"], "Quantity": [quantity], "Current Price": [100]}
    if date:
        rows["Valuation Date"] = [date]
    return csv(rows)


def ledger(account="ISA", ids=("T1", "T1"), quantities=(1, 1), currencies=("USD", "USD")):
    return csv({"Account": [account]*len(ids), "Date": ["2026-01-02"]*len(ids), "Type": ["BUY"]*len(ids),
                "Ticker": ["A"]*len(ids), "Quantity": quantities, "Price": [10]*len(ids), "Currency": currencies, "Transaction ID": ids})


def test_maya_three_accounts_reupload_and_authoritative_snapshot_selection():
    batch = ingest_files([("isa.csv", holdings()), ("pension.csv", holdings("Pension", 20)), ("gia.csv", holdings("GIA", 5))])
    assert all(i["classification"] == "legitimate" for i in review_view(batch)["issues"])
    assert review_view(batch)["can_progress"] and not review_view(batch)["review_confirmed"]
    batch = confirm_review(batch)
    assert review_view(batch)["review_confirmed"]
    batch = append_files(batch, [("renamed.csv", holdings())])
    copy_source = batch["files"][-1]["source_id"]
    duplicate = issue(batch, "exact_file_reupload")
    assert duplicate["classification"] == "confirmed_duplicate"
    assert not review_view(batch)["can_progress"] and not review_view(batch)["review_confirmed"]
    batch = decide(batch, duplicate["id"], "exclude_sources", reason="Exact copy of the ISA export already staged.", targets=[copy_source])
    assert len(review_view(batch)["retained_record_ids"]) == 3 and len(review_view(batch)["excluded_record_ids"]) == 1
    assert len(batch["files"]) == 4  # Original source survives exclusion.
    batch = append_files(batch, [("isa.csv", holdings(quantity=12, date="2026-02-01"))])
    conflict = issue(batch, "snapshot_conflict")
    assert "unknown" in str(conflict["evidence"]) and "2026-02-01" in str(conflict["evidence"])
    assert issue(batch, "same_name_changed")["classification"] == "legitimate"
    selected = batch["files"][-1]["parts"][0]["part_id"]
    batch = decide(batch, conflict["id"], "choose_snapshot", reason="Latest authoritative ISA statement replaces the earlier snapshot.", targets=[selected])
    view = review_view(batch)
    assert view["can_progress"] and len(view["retained_record_ids"]) == 3 and len(view["excluded_record_ids"]) == 2
    kept = [r for r in view["records"] if r["record_id"] in view["retained_record_ids"]]
    assert {(r["account_id"], r["data"]["Quantity"]) for r in kept} == {("ISA", 12), ("Pension", 20), ("GIA", 5)}


def james_batch():
    first = csv({"Account": ["Broker"]*3, "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
                 "Type": ["BUY", "SELL", "DEPOSIT"], "Ticker": ["US", "UK", ""], "Quantity": [1, 2, None],
                 "Price": [10, 20, None], "Amount": [None, None, 100], "Currency": ["USD", "GBP", "EUR"], "Transaction ID": ["T1", "T2", "C1"]})
    second = csv({"Account": ["Broker"]*4, "Date": ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-04"],
                  "Type": ["BUY"]*4, "Ticker": ["US", "PENCE", "PENCE", "FUND"], "Quantity": [1]*4,
                  "Price": [10, 500, 500, 2], "Amount": [None]*4, "Currency": ["USD", "GBX", "GBX", "USD"], "Transaction ID": ["T1", "", "", "T3"]})
    private = csv({"Account": ["Private"]*2, "Ticker": ["UNPRICED", "CASH"], "Quantity": [1, 25], "Current Price": [None, 1], "Currency": ["EUR", "EUR"]})
    return ingest_files([("broker1.csv", first), ("broker2.csv", second), ("private.csv", private)])


def resolve_james(batch):
    duplicate = issue(batch, "transaction_duplicate")
    record = next(r["record_id"] for r in review_view(batch)["records"] if r["record_id"] in duplicate["record_ids"] and r["source_file"] == "broker2.csv")
    batch = decide(batch, duplicate["id"], "exclude_records", reason="T1 is the same broker execution in both exports.", targets=[record])
    ambiguous = issue(batch, "transaction_match_ambiguous")
    batch = decide(batch, ambiguous["id"], "keep_both", reason="These are separate GBX executions without broker identifiers.")
    overlap = issue(batch, "ledger_period_overlap")
    return decide(batch, overlap["id"], "keep_both", reason="Repeated T1 excluded; remaining overlapping-period activity is distinct.")


def test_james_duplicate_excluded_ambiguous_legitimate_fills_retained_with_full_provenance():
    batch = james_batch()
    originals = deepcopy(batch["files"])
    assert {"transaction_duplicate", "transaction_match_ambiguous", "ledger_period_overlap"} <= {i["code"] for i in review_view(batch)["issues"]}
    batch = resolve_james(batch)
    view = review_view(batch)
    assert view["can_progress"] and len(view["records"]) == 9 and len(view["retained_record_ids"]) == 8 and len(view["excluded_record_ids"]) == 1
    assert batch["files"] == originals
    retained = [r for r in view["records"] if r["record_id"] in view["retained_record_ids"]]
    assert any(r["data"].get("Signed Quantity") == -2 for r in retained)
    assert {r["data"]["Currency"] for r in retained} == {"USD", "GBP", "GBX", "EUR"}
    assert any(r["kind"] == "cashflows" and r["data"]["Amount"] == 100 for r in retained)
    assert any(r["data"].get("Ticker") == "UNPRICED" and r["data"]["Current Price"] is None for r in retained)
    saved = next(d for d in batch["review_decisions"].values() if d["action"] == "exclude_records")
    assert all(r["source_fingerprint"] and r["account_id"] and r["source_file"] and r["record_id"] for r in saved["provenance"])
    assert saved["reason"] and len(saved["excluded_record_ids"]) == 1


@pytest.mark.parametrize("ids,classification,code", [(("T1", "T1"), "confirmed_duplicate", "transaction_duplicate"),
    (("", ""), "possible_duplicate", "transaction_match_ambiguous"), (("T1", "T2"), "possible_duplicate", "transaction_match_ambiguous")])
def test_same_account_record_identity_never_automatically_deletes(ids, classification, code):
    batch = ingest_files([("ledger.csv", ledger(ids=ids))])
    view = review_view(batch)
    assert issue(batch, code)["classification"] == classification
    assert len(view["retained_record_ids"]) == 2 and not view["can_progress"]


def test_identical_transactions_in_distinct_accounts_are_legitimate():
    batch = ingest_files([("isa.csv", ledger(ids=("T1",), quantities=(1,), currencies=("USD",))),
                          ("gia.csv", ledger("GIA", ids=("T1",), quantities=(1,), currencies=("USD",)))])
    assert review_view(batch)["can_progress"]
    assert all(i["classification"] == "legitimate" for i in review_view(batch)["issues"])


def test_exact_bytes_with_explicit_distinct_accounts_are_not_double_count_claims():
    batch = ingest_files([("a.csv", b"Ticker,Quantity\nNVDA,1\n"), ("b.csv", b"Ticker,Quantity\nNVDA,1\n")])
    assert issue(batch, "exact_file_reupload")["classification"] == "possible_duplicate"
    assignments = {p["part_id"]: a for f, a in zip(batch["files"], ["ISA", "Pension"]) for p in f["parts"]}
    batch = apply_account_assignments(batch, assignments)
    assert issue(batch, "exact_file_reupload")["classification"] == "legitimate" and review_view(batch)["can_progress"]


def test_transaction_id_with_conflicting_currency_or_quantity_is_not_confirmed_duplicate():
    batch = ingest_files([("ledger.csv", ledger(quantities=(1, 2), currencies=("USD", "EUR")))])
    conflict = issue(batch, "transaction_id_conflict")
    assert conflict["classification"] == "conflict"
    assert not any(i["code"] == "transaction_duplicate" for i in review_view(batch)["issues"])


def test_snapshot_dates_are_explicit_and_snapshot_plus_ledger_requires_format_selection():
    batch = ingest_files([("old.csv", holdings(date="2026-01-01")), ("new.csv", holdings(date="2026-02-01")),
                          ("ledger.csv", ledger(ids=("T1",), quantities=(1,), currencies=("USD",)))])
    assert set(d[0] for d in issue(batch, "snapshot_conflict")["evidence"]["snapshots"].values()) == {"2026-01-01", "2026-02-01"}
    overlap = issue(batch, "snapshot_ledger_overlap")
    batch = decide(batch, overlap["id"], "choose_format", reason="The complete ledger is authoritative; snapshots are redundant.", targets=["ledger"])
    assert review_view(batch)["can_progress"] and len(review_view(batch)["retained_record_ids"]) == 1


def test_multiple_valuation_dates_within_source_block_instead_of_summing_snapshots():
    batch = ingest_files([("mixed.csv", csv({"Account": ["ISA"]*2, "Ticker": ["A"]*2, "Quantity": [1, 2], "Valuation Date": ["2026-01-01", "2026-02-01"]}))])
    assert issue(batch, "snapshot_dates_ambiguous")["blocking"] and not review_view(batch)["can_progress"]


def test_defer_reverse_and_confirmation_are_reversible_without_changing_rows():
    batch = ingest_files([("ledger.csv", ledger())])
    original = deepcopy(batch)
    duplicate = issue(batch, "transaction_duplicate")
    batch = decide(batch, duplicate["id"], "defer", reason="Waiting for broker identity evidence.")
    with pytest.raises(ValueError, match="Resolve"):
        confirm_review(batch)
    batch = reverse_decision(batch, duplicate["id"], reason="Broker evidence is now available.")
    batch = decide(batch, duplicate["id"], "exclude_records", reason="Repeated export row.", targets=[duplicate["record_ids"][-1]])
    batch = confirm_review(batch)
    assert review_view(batch)["review_confirmed"]
    batch = reverse_decision(batch, duplicate["id"], reason="Restore the row for a second review.")
    assert not review_view(batch)["can_progress"] and not review_view(batch)["review_confirmed"]
    assert batch["files"] == original["files"] and len(review_view(batch)["retained_record_ids"]) == 2
    assert [e["event"] for e in batch["review_history"]] == ["decision", "reversal", "decision", "confirmation", "reversal"]


def test_account_correction_invalidates_exclusions_and_records_reason_then_reopens_gate():
    batch = ingest_files([("a.csv", ledger(ids=("T1",), quantities=(1,), currencies=("USD",))),
                          ("b.csv", ledger(ids=("T1",), quantities=(1,), currencies=("USD",)))])
    duplicate = issue(batch, "exact_file_reupload")
    batch = decide(batch, duplicate["id"], "exclude_sources", reason="Duplicate account export.", targets=[batch["files"][1]["source_id"]])
    batch = confirm_review(batch)
    pid = batch["files"][1]["parts"][0]["part_id"]
    batch = apply_account_assignments(batch, {pid: "GIA"}, reason="The second export belongs to another account.")
    view = review_view(batch)
    assert view["invalidated_decisions"] == [duplicate["id"]]
    assert len(view["retained_record_ids"]) == 2 and not view["review_confirmed"] and not view["can_progress"]
    batch = reverse_decision(batch, duplicate["id"], reason="Exclusion was based on the incorrect account assignment.")
    assert review_view(batch)["can_progress"]
    assert any(e["event"] == "account_correction" and e["reason"] == "The second export belongs to another account." for e in batch["review_history"])


@pytest.mark.parametrize("data", [b"", b"Ticker,Quantity\n", b"Account,Portfolio,Ticker,Quantity\nISA,GIA,A,1\n"])
def test_empty_malformed_ambiguous_sources_block_until_explicitly_excluded(data):
    batch = ingest_files([("bad.csv", data), ("valid.csv", holdings())])
    bad = issue(batch, "source_invalid")
    assert not review_view(batch)["can_progress"]
    batch = decide(batch, bad["id"], "exclude_sources", reason="Malformed source omitted pending correction.", targets=[batch["files"][0]["source_id"]])
    provenance = batch["review_decisions"][bad["id"]]["source_provenance"][0]
    assert provenance["source_file"] == "bad.csv" and provenance["source_fingerprint"] == batch["files"][0]["fingerprint"]
    assert len(provenance["raw_record_ids"]) == len(batch["files"][0]["raw_records"])
    assert review_view(batch)["can_progress"]


def test_missing_account_cannot_be_acknowledged_away_and_reason_and_targets_are_validated():
    batch = ingest_files([("missing.csv", b"Ticker,Quantity\nA,1\n")])
    unresolved = issue(batch, "account_missing")
    with pytest.raises(ValueError):
        decide(batch, unresolved["id"], "keep_both", reason="Pretend the account is known.")
    with pytest.raises(ValueError, match="reason"):
        decide(batch, unresolved["id"], "defer", reason=" ")
    with pytest.raises(ValueError, match="affected source"):
        decide(batch, unresolved["id"], "exclude_sources", reason="Unknown source", targets=["not-a-source"])
    assert not review_view(batch)["can_progress"]


def test_ledger_nonoverlap_is_not_flagged_and_adding_sources_revokes_confirmation():
    a = b"Account,Date,Type,Ticker,Quantity,Price,Transaction ID\nISA,2026-01-01,BUY,A,1,10,T1\n"
    b = b"Account,Date,Type,Ticker,Quantity,Price,Transaction ID\nISA,2026-02-01,BUY,A,1,10,T2\n"
    batch = ingest_files([("a.csv", a), ("b.csv", b)])
    assert review_view(batch)["can_progress"] and not review_view(batch)["issues"]
    batch = confirm_review(batch)
    batch = append_files(batch, [("copy.csv", a)])
    assert not review_view(batch)["review_confirmed"] and not review_view(batch)["can_progress"]


def test_detection_is_deterministic_and_read_only():
    batch = james_batch()
    batch["files"][0]["raw_records"][0][2024] = "Non-economic provider metadata with a numeric column header"
    original = deepcopy(batch)
    assert review_view(batch) == review_view(batch)
    assert batch == original


def test_foreign_cash_only_ledger_with_empty_trade_columns_is_reviewable():
    raw = b"Account,Date,Type,Ticker,Quantity,Price,Amount,Currency,Transaction ID\nCash,2026-01-01,DEPOSIT,,,,100,EUR,C1\nCash,2026-01-01,DEPOSIT,,,,100,EUR,C1\n"
    batch = ingest_files([("cash.csv", raw)])
    assert batch["files"][0]["status"] == "parsed"
    assert issue(batch, "transaction_duplicate")["classification"] == "confirmed_duplicate"
    assert all(r["kind"] == "cashflows" and r["data"]["Amount"] == 100 for r in review_view(batch)["records"])
