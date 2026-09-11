from types import SimpleNamespace

import portfolio_analytics.ingestion.input_parser as input_parser


class FakeSearch:
    def __init__(self, query, **kwargs):
        self.quotes = [
            {
                "symbol": "VOO",
                "longname": "Vanguard S&P 500 ETF",
                "quoteType": "ETF",
                "exchange": "PCX",
            },
            {
                "symbol": "AAPL",
                "longname": "Apple Inc.",
                "quoteType": "EQUITY",
                "exchange": "NMS",
            },
        ]


def test_short_prefix_does_not_surface_company(monkeypatch):
    monkeypatch.setattr(input_parser, "yf", SimpleNamespace(Search=FakeSearch))
    assert input_parser.resolve_security_candidates("van") == []


def test_complete_company_word_can_surface_company(monkeypatch):
    monkeypatch.setattr(input_parser, "yf", SimpleNamespace(Search=FakeSearch))
    results = input_parser.resolve_security_candidates("Vanguard")
    assert [row["Ticker"] for row in results] == ["VOO"]


def test_exact_ticker_still_works_even_when_short(monkeypatch):
    monkeypatch.setattr(input_parser, "yf", SimpleNamespace(Search=FakeSearch))
    results = input_parser.resolve_security_candidates("VOO")
    assert results[0]["Ticker"] == "VOO"
    assert results[0]["Exact Ticker Match"] is True
