import pandas as pd

from portfolio_analytics.ingestion.parsing_engine import build_parsing_report
from portfolio_analytics.ingestion.input_parser import read_upload, standardise_ledger_for_accounting


def _hybrid_frame():
    return pd.DataFrame({
        "Symbol": ["GLD", "MSFT"],
        "Current Price": [403.35, 510.10],
        "Date": ["2026/09/09", "2026/09/09"],
        "Time": ["16:00 EDT", "16:00 EDT"],
        "Open": [406.11, 509.00],
        "High": [406.55, 512.00],
        "Low": [401.18, 506.50],
        "Volume": [8050955, 2000000],
        "Trade Date": [20260830, 20260831],
        "Purchase Price": [528.98, 495.00],
        "Quantity": [100.0, 20.0],
        "Commission": [0.99, 0.99],
        "Transaction Type": ["BUY", "SELL"],
    })


def test_hybrid_portfolio_export_routes_to_ledger():
    frame = _hybrid_frame()
    report = build_parsing_report(frame)

    assert report["classification"]["mode"] == "ledger"
    assert report["schema"]["date"]["column"] == "Trade Date"
    assert report["schema"]["price"]["column"] == "Purchase Price"
    assert report["schema"]["action"]["column"] == "Transaction Type"
    assert "hybrid portfolio export detected" in " ".join(report["classification"]["evidence"]).lower()


def test_hybrid_accounting_prefers_trade_date_and_purchase_price():
    frame = _hybrid_frame()
    clean = standardise_ledger_for_accounting(frame)

    assert clean.loc[0, "Date"] == 20260830
    assert clean.loc[0, "Price"] == 528.98
    assert clean.loc[0, "Ticker"] == "GLD"
    assert clean.loc[0, "Type"] == "BUY"
    assert "Current Price" in clean.columns


def test_hybrid_csv_upload_does_not_enter_market_data_path():
    frame = _hybrid_frame()
    payload = frame.to_csv(index=False).encode("utf-8")
    parsed = read_upload(payload, "portfolio.csv")

    assert parsed["mode"] == "ledger"
    assert parsed["transactions"] is not None
    assert parsed["parsing_report"]["schema"]["date"]["column"] == "Trade Date"
    assert parsed["parsing_report"]["schema"]["price"]["column"] == "Purchase Price"
