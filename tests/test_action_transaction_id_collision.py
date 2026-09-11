import pandas as pd

from portfolio_analytics.ingestion.input_parser import TYPE_NAMES, _find_column, build_data_quality_report, clean_transaction_ledger
from portfolio_analytics.quality.auto_clean import auto_clean_ledger


def _action_named_ledger():
    return pd.DataFrame({
        "Date": ["2024-01-01", "2024-01-02", "2024-01-03"],
        "Transaction_ID": ["TXN-00001", "TXN-00002", "TXN-00003"],
        "Action": ["BUY", "SELL", "BUY"],
        "Ticker": ["MSFT", "AAPL", "NVDA"],
        "Quantity": [10, 5, 3],
        "Price": [100.0, 200.0, 300.0],
        "Gross_Value": [1000.0, 1000.0, 900.0],
        "Fees": [1.0, 1.0, 1.0],
    })


def test_action_header_never_resolves_to_transaction_id():
    frame = _action_named_ledger()
    assert _find_column(frame, TYPE_NAMES) == "Action"


def test_data_quality_does_not_count_txn_ids_as_actions():
    frame = _action_named_ledger()
    clean, issues = clean_transaction_ledger(frame)
    report = build_data_quality_report(frame, clean, issues)
    assert report["Unrecognised Actions"] == 0
    assert report["Valid Transactions"] == 3


def test_auto_clean_reads_action_not_transaction_id():
    frame = _action_named_ledger()
    _, report = auto_clean_ledger(frame)
    audit = report["audit_table"]
    assert audit["Original Action"].tolist() == ["BUY", "SELL", "BUY"]
    assert not audit["Original Action"].astype(str).str.startswith("TXN-").any()
