"""Read, classify and parse uploaded portfolio files."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .normalizer import detect_column_map, normalise_holdings, normalise_ledger


#______________________________________________________________________________
# FILE READING
#______________________________________________________________________________

def read_portfolio_file(data: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename or "").suffix.lower()

    if suffix == ".csv":
        text = data.decode("utf-8-sig", errors="replace")
        return pd.read_csv(StringIO(text))

    if suffix in {".xlsx", ".xlsm"}:
        workbook = pd.ExcelFile(BytesIO(data))
        if not workbook.sheet_names:
            raise ValueError("Excel workbook contains no worksheets.")
        # v4 deliberately starts with the first visible data sheet. Sheet selection
        # can be added later without changing the parser contract.
        return pd.read_excel(workbook, sheet_name=workbook.sheet_names[0])

    raise ValueError("Upload a CSV or XLSX portfolio file.")


#______________________________________________________________________________
# PORTFOLIO TYPE DETECTION
#______________________________________________________________________________

def detect_portfolio_type(frame: pd.DataFrame) -> tuple[str, float, dict[str, str], list[str]]:
    """Classify an uploaded table as holdings or ledger during parsing."""
    if frame is None or frame.empty:
        raise ValueError("Portfolio file is empty.")

    column_map = detect_column_map(frame)
    notes: list[str] = []

    has_ticker = "ticker" in column_map
    has_quantity = "quantity" in column_map
    has_date = "date" in column_map
    has_type = "type" in column_map

    type_values: set[str] = set()
    if has_type:
        type_values = set(
            frame[column_map["type"]]
            .dropna()
            .astype(str)
            .str.upper()
            .str.strip()
            .head(500)
            .tolist()
        )

    transaction_signals = bool(type_values & {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND"})

    if has_date and has_type and transaction_signals:
        if not (has_ticker and has_quantity) and not (type_values <= {"DEPOSIT", "WITHDRAWAL", "DIVIDEND"}):
            notes.append("Transaction-like rows were detected but ticker/quantity columns are incomplete.")
        return "ledger", 0.99, column_map, notes

    if has_ticker and has_quantity:
        confidence = 0.98 if not has_date else 0.90
        if has_date:
            notes.append("A date column exists, but no BUY/SELL transaction pattern was detected; treated as holdings.")
        return "holdings", confidence, column_map, notes

    raise ValueError(
        "Could not identify the portfolio structure. Holdings need Ticker + Quantity. "
        "Ledgers need Date + Type plus Ticker + Quantity for trade rows."
    )


#______________________________________________________________________________
# PARSE + NORMALISE
#______________________________________________________________________________

def parse_dataframe(
    frame: pd.DataFrame,
    *,
    source_kind: str = "upload",
    filename: str = "",
    starting_cash: float = 0.0,
) -> dict[str, Any]:
    classification, confidence, column_map, notes = detect_portfolio_type(frame)

    raw_records = frame.where(pd.notna(frame), None).to_dict(orient="records")

    if classification == "holdings":
        holdings, issues = normalise_holdings(frame, column_map=column_map)
        ledger = pd.DataFrame()
        cashflows = pd.DataFrame()
    else:
        ledger, cashflows, issues = normalise_ledger(frame, column_map=column_map)
        holdings = pd.DataFrame()

    if classification == "holdings":
        dated_mask = holdings.get("Purchase Date", pd.Series(dtype="datetime64[ns]")).notna() if not holdings.empty else pd.Series(dtype=bool)
        basis_mask = holdings.get("Average Entry Price", pd.Series(dtype=float)).notna() if not holdings.empty else pd.Series(dtype=bool)
        non_cash_mask = holdings.get("Ticker", pd.Series(dtype=str)).astype(str).str.upper().ne("CASH") if not holdings.empty else pd.Series(dtype=bool)
        reconstructable = int((dated_mask & basis_mask & non_cash_mask).sum()) if len(holdings) else 0
        history_capability = {
            "available": reconstructable > 0,
            "method": "reconstructed dated holdings" if reconstructable > 0 else None,
            "dated_positions": reconstructable,
        }
    else:
        history_capability = {
            "available": not ledger.empty,
            "method": "actual transaction ledger" if not ledger.empty else None,
            "dated_positions": int(len(ledger)),
        }

    return {
        "source": {
            "kind": source_kind,
            "filename": filename,
        },
        "classification": classification,
        "confidence": float(confidence),
        "column_map": column_map,
        "notes": notes,
        "issues": issues,
        "inputs": {
            "starting_cash": float(starting_cash),
        },
        "user_dataset": {
            "columns": [str(c) for c in frame.columns],
            "records": raw_records,
        },
        "normalised_dataset": {
            "holdings": holdings.where(pd.notna(holdings), None).to_dict(orient="records"),
            "ledger": ledger.where(pd.notna(ledger), None).to_dict(orient="records"),
            "cashflows": cashflows.where(pd.notna(cashflows), None).to_dict(orient="records"),
        },
        "variables": {
            "history_capability": history_capability,
            "tickers": sorted(
                set(
                    holdings.get("Ticker", pd.Series(dtype=str)).astype(str).tolist()
                    + ledger.get("Ticker", pd.Series(dtype=str)).astype(str).tolist()
                )
                - {""}
            ),
            "provided_prices": {
                str(row["Ticker"]): float(row["Current Price"])
                for _, row in holdings.iterrows()
                if pd.notna(row.get("Current Price"))
            },
        },
    }


def parse_upload(data: bytes, filename: str, *, starting_cash: float = 0.0) -> dict[str, Any]:
    frame = read_portfolio_file(data, filename)
    return parse_dataframe(
        frame,
        source_kind="upload",
        filename=filename,
        starting_cash=starting_cash,
    )
