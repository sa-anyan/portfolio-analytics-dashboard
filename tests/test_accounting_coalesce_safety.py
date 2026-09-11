import pandas as pd

from input_parser import standardise_ledger_for_accounting


def _base_frame():
    return pd.DataFrame({
        "Date": ["2024-01-01", "2024-01-02"],
        "Type": ["BUY", "SELL"],
        "Ticker": ["AAPL", "MSFT"],
        "Quantity": [10, 5],
        "Price": [100.0, 200.0],
    })


def test_price_metadata_is_not_merged_into_price():
    frame = _base_frame()
    frame["Derived Price Method"] = ["LATEST_CLOSE", "HISTORICAL_CLOSE"]
    frame["Price Change %"] = [0.10, -0.05]
    frame["Price Source"] = ["Yahoo", "Upload"]

    out = standardise_ledger_for_accounting(frame)

    assert out["Price"].tolist() == [100.0, 200.0]
    assert out["Derived Price Method"].tolist() == ["LATEST_CLOSE", "HISTORICAL_CLOSE"]
    assert out["Price Source"].tolist() == ["Yahoo", "Upload"]


def test_exact_price_alias_can_fill_blank_price_without_dtype_crash():
    frame = _base_frame()
    frame.loc[1, "Price"] = None
    frame["Current Price"] = ["100.00", "205.50"]

    out = standardise_ledger_for_accounting(frame)

    assert float(out.loc[0, "Price"]) == 100.0
    assert float(out.loc[1, "Price"]) == 205.5
    assert "Current Price" not in out.columns


def test_generic_price_fallback_does_not_consume_price_metadata():
    frame = _base_frame().drop(columns=["Price"])
    frame["broker_last_price"] = [101.0, 199.0]
    frame["Derived Price Method"] = ["A", "B"]

    out = standardise_ledger_for_accounting(frame)

    assert out["Price"].tolist() == [101.0, 199.0]
    assert out["Derived Price Method"].tolist() == ["A", "B"]
