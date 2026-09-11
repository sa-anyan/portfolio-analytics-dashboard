from __future__ import annotations

from portfolio_analytics.ingestion.parsing_engine import infer_schema, classify_frame
from tests.corruption_engine import rename_headers


def test_fragmented_headers_still_map_to_financial_roles(clean_transactions):
    corrupted, _ = rename_headers(clean_transactions)
    schema = infer_schema(corrupted)

    assert schema["ticker"]["column"] == "tickerest_2"
    assert schema["quantity"]["column"] == "net_shares_held"
    assert schema["date"]["column"] == "broker_transaction_date"
    assert schema["action"]["column"] == "transaction_direction"
    assert schema["price"]["column"] == "last_price_used"
    assert schema["fees"]["column"] == "broker_commission_usd"

    classification = classify_frame(corrupted, schema)
    assert classification["mode"] == "ledger"
