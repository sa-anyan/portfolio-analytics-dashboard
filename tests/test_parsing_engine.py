from __future__ import annotations

import pandas as pd

from portfolio_analytics.ingestion.parsing_engine import (
    analyse_date_column,
    build_parsing_report,
    infer_schema,
    score_term_match,
)


def run_tests():
    score, rule = score_term_match("tickerest_2", "ticker")
    assert score >= 75, (score, rule)

    holdings = pd.DataFrame({
        "tickerrest": ["AAPL", "MSFT"],
        "net_shares": [10, 20],
        "last_price_used": [100, 200],
    })
    report = build_parsing_report(holdings)
    assert report["classification"]["mode"] == "holdings"
    assert report["schema"]["ticker"]["column"] == "tickerrest"
    assert report["schema"]["quantity"]["column"] == "net_shares"
    assert report["schema"]["price"]["column"] == "last_price_used"
    assert report["required_variables_valid"] is True

    ledger = pd.DataFrame({
        "Trade_Date": ["18/01/2026", "03/04/2026"],
        "Transaction_Action": ["BUY", "SELL"],
        "StockTicker": ["AAPL", "AAPL"],
        "ShareQty": [10, 2],
        "Execution_Price": [100, 110],
        "Txn_ID": ["1", "2"],
        "Commission": [1, 1],
    })
    report = build_parsing_report(ledger)
    assert report["classification"]["mode"] == "ledger"
    assert report["date_profile"]["suggested_format"] == "DD/MM/YYYY"
    assert report["date_profile"]["requires_user_choice"] is False

    ambiguous = pd.DataFrame({
        "Date": ["03/04/2026", "04/05/2026"],
        "Ticker": ["AAPL", "MSFT"],
        "Quantity": [1, 2],
        "Action": ["BUY", "BUY"],
    })
    schema = infer_schema(ambiguous)
    profile = analyse_date_column(ambiguous, schema)
    assert profile["requires_user_choice"] is True

    print("Schema-first parser tests passed.")


if __name__ == "__main__":
    run_tests()
