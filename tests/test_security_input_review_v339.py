from __future__ import annotations

import pandas as pd

import portfolio_analytics.ingestion.input_parser as input_parser


def test_invalid_security_and_quantity_are_preserved_for_review(monkeypatch):
    def fake_resolve_security(value):
        text = str(value).strip()
        if text.lower() == "samuel":
            return {
                "Ticker": None,
                "Asset Name": None,
                "Asset Type": None,
                "Exchange": None,
                "Match Status": "Not found",
            }
        return {
            "Ticker": text.upper(),
            "Asset Name": text.upper(),
            "Asset Type": "EQUITY",
            "Exchange": "TEST",
            "Match Status": "Valid ticker",
        }

    monkeypatch.setattr(input_parser, "resolve_security", fake_resolve_security)

    working = pd.DataFrame([
        {
            "Security": "AAPL",
            "Quantity": "10",
            "Provided Price": 100.0,
            "Provided Entry Price": 90.0,
        },
        {
            "Security": "samuel",
            "Quantity": "notyet",
            "Provided Price": None,
            "Provided Entry Price": None,
        },
    ])

    valid, review = input_parser.validate_holdings(working)

    assert len(valid) == 1
    assert len(review) == 1
    assert review.iloc[0]["Input"] == "samuel"
    assert str(review.iloc[0]["Original Quantity"]) == "notyet"
    status = str(review.iloc[0]["Status"]).lower()
    assert "security not found" in status
    assert "quantity" in status
