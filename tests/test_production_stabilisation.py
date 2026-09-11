import pandas as pd

from input_parser import (
    standardise_ledger_for_accounting,
    ledger_security_inputs,
)
from today_engine import prepare_transactions
from repair_engine import diagnose_repairs


def test_accounting_standardiser_coalesces_alias_collisions():
    frame = pd.DataFrame({
        'Date': ['2026-01-01', '2026-01-02'],
        'Type': ['BUY', ''],
        'Action': ['', 'SELL'],
        'Ticker': ['AAPL', 'MSFT'],
        'Quantity': [10, 5],
        'Price': [100, 200],
    })
    out = standardise_ledger_for_accounting(frame)
    assert not out.columns.duplicated().any()
    assert list(out['Type']) == ['BUY', 'SELL']


def test_prepare_transactions_survives_duplicate_labels():
    frame = pd.DataFrame([
        ['2026-01-01', '2026-01-01', 'BUY', 'BUY', 'AAPL', 10, 100],
        ['2026-01-02', '', 'SELL', '', 'AAPL', 5, 110],
    ], columns=['Date', 'Date', 'Type', 'Type', 'Ticker', 'Quantity', 'Price'])
    out = prepare_transactions(frame)
    assert len(out) == 2
    assert not out.columns.duplicated().any()


def test_transaction_id_is_never_treated_as_security():
    frame = pd.DataFrame({
        'Transaction_ID': ['TXN-00001', 'TXN-00002'],
        'Type': ['BUY', 'SELL'],
        'Quantity': [10, 5],
        'Price': [100, 110],
        'Date': ['2026-01-01', '2026-01-02'],
    })
    assert ledger_security_inputs(frame) == []


def test_security_code_is_still_recognised():
    frame = pd.DataFrame({
        'Security Code': ['AAPL', 'MSFT'],
        'Quantity': [10, 5],
    })
    assert ledger_security_inputs(frame) == ['AAPL', 'MSFT']


def test_missing_transaction_id_is_warning_not_manual_blocker():
    frame = pd.DataFrame({
        'Date': ['2026-01-01'],
        'Transaction_ID': [None],
        'Type': ['BUY'],
        'Ticker': ['AAPL'],
        'Asset': ['Apple'],
        'Quantity': [10],
        'Price': [100.0],
        'Gross_Value': [1000.0],
        'Fees': [1.0],
        'Currency': ['USD'],
    })
    _, diagnostics = diagnose_repairs(frame)
    manual = diagnostics['manual_questions']
    warnings = diagnostics['audit_warnings']
    if not manual.empty:
        assert 'Transaction ID' not in set(manual['Field'])
    assert 'Transaction ID' in set(warnings['Field'])
