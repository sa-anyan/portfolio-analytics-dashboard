"""Manual portfolio input path."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import pandas as pd

from .parser import parse_dataframe


#______________________________________________________________________________
# MANUAL HOLDINGS PARSER
#______________________________________________________________________________

def parse_manual_holdings(
    rows: list[dict[str, Any]] | pd.DataFrame,
    *,
    starting_cash: float = 0.0,
) -> dict[str, Any]:
    """Send manual holdings through the same normalisation contract as uploads."""
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)

    if frame.empty:
        raise ValueError("Enter at least one holding.")

    parsed = parse_dataframe(
        frame,
        source_kind="manual",
        filename="manual-entry",
        starting_cash=starting_cash,
    )

    if parsed["classification"] != "holdings":
        raise ValueError("Manual entry currently accepts holdings, not transaction ledgers.")

    return parsed
