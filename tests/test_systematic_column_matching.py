import pandas as pd

from input_parser import _find_column, SECURITY_NAMES, TYPE_NAMES, DATE_NAMES
from parsing_engine import build_parsing_report, score_column_role


def test_exact_alias_beats_generic_token_match():
    frame = pd.DataFrame(columns=["Security Type", "Transaction Type", "Transaction_ID"])
    assert _find_column(frame, TYPE_NAMES) == "Transaction Type"


def test_action_never_matches_inside_transaction_word():
    frame = pd.DataFrame(columns=["Transaction_ID", "Ticker", "Quantity"])
    assert _find_column(frame, TYPE_NAMES) is None
    scored = score_column_role("Transaction_ID", "action")
    assert scored["score"] < 60


def test_security_type_is_not_an_action_column():
    frame = pd.DataFrame(columns=["Security Type", "Ticker", "Quantity"])
    assert _find_column(frame, TYPE_NAMES) is None
    assert score_column_role("Security Type", "action")["score"] < 60


def test_transaction_reference_maps_to_transaction_id_not_ticker():
    report = build_parsing_report(pd.DataFrame({
        "Transaction Reference": ["TXN-1"],
        "Ticker": ["AAPL"],
        "Transaction Type": ["BUY"],
        "Quantity": [1],
        "Date": ["2026-01-01"],
        "Price": [100.0],
    }))
    schema = report["schema"]
    assert schema["transaction_id"]["column"] == "Transaction Reference"
    assert schema["ticker"]["column"] == "Ticker"


def test_fragmented_ticker_header_still_supported():
    frame = pd.DataFrame(columns=["tickerest_2", "transaction_direction", "broker_transaction_date"])
    assert _find_column(frame, SECURITY_NAMES) == "tickerest_2"
    assert _find_column(frame, TYPE_NAMES) == "transaction_direction"
    assert _find_column(frame, DATE_NAMES) == "broker_transaction_date"


def test_account_type_does_not_become_transaction_action():
    assert score_column_role("Account Type", "action")["score"] < 60


def test_known_action_aliases_still_work():
    for header in ["Action", "Side", "Trade Side", "Order Side", "Transaction Type"]:
        assert score_column_role(header, "action")["score"] >= 90
