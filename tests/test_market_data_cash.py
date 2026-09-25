from portfolio_analytics.core.market_data import _clean_tickers


def test_cash_is_excluded_from_market_data_requests():
    assert _clean_tickers(["AAPL", "cash", "MSFT", "CASH"]) == ["AAPL", "MSFT"]
