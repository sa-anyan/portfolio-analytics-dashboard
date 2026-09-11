from pathlib import Path

from portfolio_analytics.ingestion.input_parser import read_upload


def test_a2m_csv_infers_ticker_from_filename():
    fixture = Path(__file__).parent / "fixtures" / "a2m_market_data_sample.csv"
    parsed = read_upload(fixture.read_bytes(), "A2M.csv")
    assert parsed["mode"] == "market_data"
    assert parsed["inferred_ticker"] == "A2M"
    assert parsed["ticker_source"] == "filename"
    assert parsed["parsing_report"]["schema"]["adjusted_close"]["column"] == "Adj Close"
