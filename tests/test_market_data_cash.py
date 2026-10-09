from portfolio_analytics.core.market_data import _clean_tickers


def test_cash_is_excluded_from_market_data_requests():
    assert _clean_tickers(["AAPL", "cash", "MSFT", "CASH"]) == ["AAPL", "MSFT"]


def test_latest_price_dates_are_actual_per_security_observations(monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    from portfolio_analytics.core import market_data
    frame = pd.DataFrame({("Close", "A"): [10., float("nan")],
                          ("Close", "B"): [20., 21.]}, index=pd.to_datetime(["2026-10-01", "2026-10-09"]))
    monkeypatch.setattr(market_data, "_import_yfinance", lambda: SimpleNamespace(download=lambda **kwargs: frame))
    prices, metadata = market_data.fetch_latest_prices(["A", "B"])
    assert prices == {"A": 10., "B": 21.}
    assert metadata["observations"]["A"]["as_of"].startswith("2026-10-01")
    assert metadata["observations"]["B"]["as_of"].startswith("2026-10-09")
