from pathlib import Path

import pandas as pd

from parsing_engine import build_parsing_report


EXPECTED_MARKET_ROLES = {
    "date": "Date",
    "ticker": "Ticker",
    "open_price": "Open",
    "high_price": "High",
    "low_price": "Low",
    "close_price": "Close",
    "adjusted_close": "Adjusted",
    "returns": "Returns",
    "volume": "Volume",
}


def _sp500_fixture() -> pd.DataFrame:
    fixture = Path(__file__).parent / "fixtures" / "sp500_market_data_sample.csv"
    return pd.read_csv(fixture)


def test_sp500_price_history_is_classified_as_market_data():
    report = build_parsing_report(_sp500_fixture())

    assert report["classification"]["mode"] == "market_data"
    assert report["required_variables_valid"] is True
    assert report["classification"]["market_data_score"] >= 10


def test_sp500_market_columns_map_to_distinct_semantic_roles():
    report = build_parsing_report(_sp500_fixture())
    schema = report["schema"]

    for role, expected_column in EXPECTED_MARKET_ROLES.items():
        assert schema[role]["column"] == expected_column
        assert schema[role]["score"] >= 90


def test_market_data_does_not_require_quantity():
    report = build_parsing_report(_sp500_fixture())

    assert "quantity" not in report["schema"]
    assert report["required_variables_valid"] is True


def _a2m_fixture() -> pd.DataFrame:
    fixture = Path(__file__).parent / "fixtures" / "a2m_market_data_sample.csv"
    return pd.read_csv(fixture)


def test_single_security_ohlc_without_ticker_is_market_data():
    report = build_parsing_report(_a2m_fixture())
    assert report["classification"]["mode"] == "market_data"
    assert report["required_variables_valid"] is True
    assert "ticker" not in report["schema"]
    assert report["schema"]["adjusted_close"]["column"] == "Adj Close"
