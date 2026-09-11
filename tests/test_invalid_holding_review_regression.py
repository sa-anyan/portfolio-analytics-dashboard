import pandas as pd

from input_parser import prepare_holdings_for_review


def test_invalid_quantity_row_survives_preparation_for_human_review():
    source = pd.DataFrame({
        "Symbol": ["AAPL", "samuel"],
        "Quantity": [100, "notyet"],
    })

    working = prepare_holdings_for_review(source)

    bad = working.loc[working["Security"].eq("samuel")]
    assert len(bad) == 1
    assert bad.iloc[0]["Quantity"] == "notyet"
    assert bad.iloc[0]["Source Record"] == {
        "Symbol": "samuel",
        "Quantity": "notyet",
    }
