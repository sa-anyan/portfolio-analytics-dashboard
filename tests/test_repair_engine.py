from __future__ import annotations
import sys, types
import pandas as pd
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
from repair_engine import diagnose_repairs, apply_repairs


def _find(suggestions, row, field):
    match = suggestions[(suggestions["Source Row"] == row) & (suggestions["Field"] == field)]
    assert len(match) == 1, (row, field, match)
    return match.iloc[0]


def run_tests():
    path = "/mnt/data/portfolio_missing_cells_test (1).xlsx"
    frame = pd.read_excel(path, sheet_name="Transactions")
    suggestions, diagnostics = diagnose_repairs(frame)

    # Deterministic/contextual repairs from the benchmark fixture.
    assert _find(suggestions, 26, "Ticker")["Proposed Value"] == "MSFT"
    assert abs(float(_find(suggestions, 41, "Quantity")["Proposed Value"]) - 5.0) < 1e-9
    assert abs(float(_find(suggestions, 56, "Price")["Proposed Value"]) - 196.408) < 1e-6
    assert _find(suggestions, 101, "Ticker")["Proposed Value"] in {"BTC", "BTC-USD"}
    assert abs(float(_find(suggestions, 116, "Quantity")["Proposed Value"]) - 100.0) < 1e-9
    assert _find(suggestions, 130, "Gross Value")["Repair Type"] == "RECONSTRUCTED"
    fee = _find(suggestions, 100, "Fees")
    assert fee["Repair Type"] == "ESTIMATED"
    assert abs(float(fee["Proposed Value"]) - 21.11) <= 0.02
    assert _find(suggestions, 145, "Asset")["Proposed Value"] == "Tesla"
    assert _find(suggestions, 146, "Currency")["Proposed Value"] == "USD"

    # Unsafe fields are questions, not fabricated repairs.
    manual = diagnostics["manual_questions"]
    assert ((manual["Source Row"] == 70) & (manual["Field"] == "Date")).any()
    assert ((manual["Source Row"] == 86) & (manual["Field"] == "Action")).any()
    assert ((manual["Source Row"] == 115) & (manual["Field"] == "Transaction ID")).any()

    # Double-missing rows cannot be reconstructed from row identities.
    assert suggestions[(suggestions["Source Row"] == 131) & (suggestions["Field"].isin(["Quantity", "Gross Value"]))].empty
    assert suggestions[(suggestions["Source Row"] == 147) & (suggestions["Field"].isin(["Price", "Gross Value"]))].empty

    repaired, log = apply_repairs(frame, suggestions)
    assert not log.empty
    print("Repair engine regression tests passed.")


if __name__ == "__main__":
    run_tests()
