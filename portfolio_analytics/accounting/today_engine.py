
#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pandas as pd

from portfolio_analytics.ingestion.input_parser import clean_number, clean_date


#______________________________________________________________________________
# TICKER ALIASES
#______________________________________________________________________________

TICKER_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


def standardise_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    return TICKER_ALIASES.get(ticker, ticker)




def _coalesce_duplicate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a frame with unique labels, coalescing duplicate columns safely."""
    if not frame.columns.duplicated().any():
        return frame.copy()

    rebuilt = pd.DataFrame(index=frame.index)
    for name in dict.fromkeys(frame.columns):
        block = frame.loc[:, frame.columns == name]
        combined = block.iloc[:, 0].copy()
        for i in range(1, block.shape[1]):
            blank = combined.isna() | combined.astype(str).str.strip().isin(["", "nan", "None"])
            combined.loc[blank] = block.iloc[:, i].loc[blank]
        rebuilt[name] = combined
    return rebuilt

def _columns(frame: pd.DataFrame) -> dict[str, str]:
    return {
        str(c).strip().lower().replace("_", " "): c
        for c in frame.columns
    }


#______________________________________________________________________________
# PREPARE TRANSACTION LEDGER
#______________________________________________________________________________

def prepare_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardise a BUY/SELL transaction table."""
    data = _coalesce_duplicate_columns(frame)
    lookup = _columns(data)

    required = {"date", "type", "ticker", "quantity"}
    if not required.issubset(lookup):
        raise ValueError(
            "Transaction data needs Date, Type, Ticker and Quantity columns."
        )

    rename = {
        lookup["date"]: "Date",
        lookup["type"]: "Type",
        lookup["ticker"]: "Ticker",
        lookup["quantity"]: "Quantity",
    }

    for source, target in [
        ("price", "Price"),
        ("gross value", "Gross Value"),
        ("fees", "Fees"),
        ("asset", "Asset Name"),
        ("transaction id", "Transaction ID"),
    ]:
        if source in lookup:
            rename[lookup[source]] = target

    data = data.rename(columns=rename)
    data["Date"] = data["Date"].map(clean_date)
    data["Type"] = data["Type"].astype(str).str.upper().str.strip()
    data["Ticker"] = data["Ticker"].map(standardise_ticker)
    data["Quantity"] = data["Quantity"].map(clean_number)

    # BUY/SELL determines direction. Quantity is always trade size.
    data["Quantity"] = data["Quantity"].abs()

    if "Price" in data:
        data["Price"] = data["Price"].map(clean_number)
    if "Gross Value" in data:
        data["Gross Value"] = data["Gross Value"].map(clean_number)
    if "Fees" not in data:
        data["Fees"] = 0.0
    else:
        data["Fees"] = data["Fees"].map(clean_number).fillna(0.0)

    data = data[
        data["Date"].notna()
        & data["Type"].isin(["BUY", "SELL"])
        & data["Quantity"].notna()
        & (data["Quantity"] > 0)
    ].copy()

    if "Price" not in data and "Gross Value" not in data:
        raise ValueError(
            "Transactions need Price or Gross Value so entry prices and cash can be reconstructed."
        )

    if "Price" not in data:
        data["Price"] = data["Gross Value"].abs() / data["Quantity"]

    data = data.sort_values(["Date"], kind="stable").reset_index(drop=True)
    return data


#______________________________________________________________________________
# PREPARE CASH FLOWS
#______________________________________________________________________________

def prepare_cashflows(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Standardise DEPOSIT/WITHDRAWAL/DIVIDEND rows."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", "Type", "Amount"])

    data = frame.copy()
    lookup = _columns(data)
    if not {"date", "type", "amount"}.issubset(lookup):
        return pd.DataFrame(columns=["Date", "Type", "Amount"])

    data = data.rename(columns={
        lookup["date"]: "Date",
        lookup["type"]: "Type",
        lookup["amount"]: "Amount",
    })
    data["Date"] = data["Date"].map(clean_date)
    data["Type"] = data["Type"].astype(str).str.upper().str.strip()
    data["Amount"] = data["Amount"].map(clean_number).fillna(0.0)
    return data[
        data["Date"].notna()
        & data["Type"].isin(["DEPOSIT", "WITHDRAWAL", "DIVIDEND"])
    ][["Date", "Type", "Amount"]].sort_values("Date")


#______________________________________________________________________________
# POSITION ACCOUNTING
#______________________________________________________________________________

@dataclass
class PositionState:
    quantity: float = 0.0
    average_entry_price: float = 0.0
    realised_pnl: float = 0.0


def _apply_trade(
    state: PositionState,
    trade_type: str,
    quantity: float,
    price: float,
) -> float:
    """
    Update one position using average-cost accounting.

    Returns realised P&L generated by this trade.
    Long quantity is positive; short quantity is negative.
    """
    signed_trade = quantity if trade_type == "BUY" else -quantity
    old_q = state.quantity
    new_q = old_q + signed_trade
    realised = 0.0

    # Opening/increasing a position in the same direction.
    if old_q == 0 or old_q * signed_trade > 0:
        old_abs = abs(old_q)
        add_abs = abs(signed_trade)
        total_abs = old_abs + add_abs
        state.average_entry_price = (
            (old_abs * state.average_entry_price + add_abs * price)
            / total_abs
        )
        state.quantity = new_q
        return realised

    # Trade is reducing, closing or reversing the existing position.
    closing_qty = min(abs(old_q), abs(signed_trade))

    if old_q > 0:
        # Selling a long: sell price - average entry.
        realised = closing_qty * (price - state.average_entry_price)
    else:
        # Buying to cover a short: average short entry - cover price.
        realised = closing_qty * (state.average_entry_price - price)

    state.realised_pnl += realised

    if new_q == 0:
        state.quantity = 0.0
        state.average_entry_price = 0.0
    elif old_q * new_q > 0:
        # Partial close; remaining position keeps its old basis.
        state.quantity = new_q
    else:
        # Reversal: excess trade opens a new position at this trade price.
        state.quantity = new_q
        state.average_entry_price = price

    return realised


#______________________________________________________________________________
# BUILD CURRENT ACCOUNT
#______________________________________________________________________________

def build_current_account(
    transactions: pd.DataFrame,
    cashflows: pd.DataFrame | None = None,
    starting_free_cash: float = 0.0,
    as_of_date: Any = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """
    Reconstruct today's open positions and cash.

    Cash accounting:
      BUY  -> cash decreases by trade value + fees
      SELL -> cash increases by trade value - fees
      DEPOSIT/WITHDRAWAL/DIVIDEND -> cash changes explicitly

    This means short-sale proceeds enter the account cash balance while the
    open short is simultaneously carried as a negative market-value liability.
    Realised P&L is NOT separately added to cash because it is already embedded
    in the sale/cover cash flows.
    """
    tx = prepare_transactions(transactions)
    cf = prepare_cashflows(cashflows)

    as_of = None

    if as_of_date is not None:
        as_of = pd.Timestamp(as_of_date)
        tx = tx[
            tx["Date"] <= as_of
        ].copy()
        cf = cf[
            cf["Date"] <= as_of
        ].copy()

    events = []
    for _, row in cf.iterrows():
        events.append({
            "Date": row["Date"],
            "Kind": "CASHFLOW",
            "Type": row["Type"],
            "Amount": float(row["Amount"]),
        })
    for _, row in tx.iterrows():
        events.append({
            "Date": row["Date"],
            "Kind": "TRADE",
            "Type": row["Type"],
            "Ticker": row["Ticker"],
            "Quantity": float(row["Quantity"]),
            "Price": float(row["Price"]),
            "Fees": float(row.get("Fees", 0.0)),
        })

    events = sorted(events, key=lambda x: x["Date"])
    cash = float(starting_free_cash)
    states: dict[str, PositionState] = {}
    ledger_rows = []

    for event in events:
        cash_before = cash

        if event["Kind"] == "CASHFLOW":
            amount = event["Amount"]
            if event["Type"] in {"DEPOSIT", "DIVIDEND"}:
                cash += amount
                cash_change = amount
            else:
                cash -= amount
                cash_change = -amount

            ledger_rows.append({
                "Date": event["Date"],
                "Event": event["Type"],
                "Ticker": "",
                "Quantity": 0.0,
                "Price": 0.0,
                "Cash Before": cash_before,
                "Cash Change": cash_change,
                "Cash After": cash,
                "Realised P&L": 0.0,
            })
            continue

        ticker = event["Ticker"]
        state = states.setdefault(ticker, PositionState())
        trade_value = event["Quantity"] * event["Price"]
        fees = event["Fees"]

        if event["Type"] == "BUY":
            cash_change = -trade_value - fees
        else:
            cash_change = trade_value - fees

        cash += cash_change
        realised = _apply_trade(
            state,
            event["Type"],
            event["Quantity"],
            event["Price"],
        )

        ledger_rows.append({
            "Date": event["Date"],
            "Event": event["Type"],
            "Ticker": ticker,
            "Quantity": event["Quantity"],
            "Price": event["Price"],
            "Cash Before": cash_before,
            "Cash Change": cash_change,
            "Cash After": cash,
            "Realised P&L": realised,
        })

    positions = []
    for ticker, state in states.items():
        if abs(state.quantity) < 1e-12:
            continue
        positions.append({
            "Ticker": ticker,
            "Position": "LONG" if state.quantity > 0 else "SHORT",
            "Quantity": state.quantity,
            "Average Entry Price": state.average_entry_price,
            "Realised P&L": state.realised_pnl,
        })

    summary = {
        "Cash Balance": cash,
        "Starting Free Cash": float(starting_free_cash),
        "Explicit Net Cash Flows": float(
            sum(
                r["Cash Change"]
                for r in ledger_rows
                if r["Event"] in {"DEPOSIT", "WITHDRAWAL"}
            )
        ),
        "As Of": as_of,
    }

    return (
        pd.DataFrame(positions),
        pd.DataFrame(ledger_rows),
        summary,
    )



#______________________________________________________________________________
# HISTORICAL ACCOUNT EQUITY FROM A TRANSACTION LEDGER
#______________________________________________________________________________

def build_historical_account_equity(
    transactions: pd.DataFrame,
    cashflows: pd.DataFrame | None,
    closing_prices: pd.DataFrame,
    starting_free_cash: float = 0.0,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Reconstruct dated account equity for a BUY/SELL ledger.

    The curve uses actual historical holdings, including short positions, plus
    the cash generated by trades and explicit cash events. Deposits and
    withdrawals are treated as external flows when calculating investment
    returns; dividends remain investment return.

    No missing security price is silently replaced with zero. Dates on which an
    open position cannot be valued remain unavailable until a usable price is
    present.
    """
    tx = prepare_transactions(transactions)
    cf = prepare_cashflows(cashflows)

    if tx.empty or closing_prices is None or closing_prices.empty:
        return pd.DataFrame(), []

    prices = closing_prices.copy()
    prices.index = pd.to_datetime(prices.index, errors="coerce").normalize()
    prices = prices[~prices.index.isna()].sort_index()
    prices = prices.loc[~prices.index.duplicated(keep="last")]

    available_tickers = [str(column) for column in prices.columns]
    missing_tickers = sorted(
        set(tx["Ticker"].astype(str)) - set(available_tickers)
    )

    tx = tx[tx["Ticker"].isin(available_tickers)].copy()
    if tx.empty:
        return pd.DataFrame(), missing_tickers

    tx["Date"] = pd.to_datetime(tx["Date"], errors="coerce").dt.normalize()
    cf["Date"] = pd.to_datetime(cf["Date"], errors="coerce").dt.normalize()

    first_event_candidates = [tx["Date"].min()]
    if not cf.empty:
        first_event_candidates.append(cf["Date"].min())
    first_event = min(value for value in first_event_candidates if pd.notna(value))

    event_dates = pd.DatetimeIndex(tx["Date"].dropna().unique())
    if not cf.empty:
        event_dates = event_dates.union(
            pd.DatetimeIndex(cf["Date"].dropna().unique())
        )

    timeline = prices.index.union(event_dates).sort_values()
    timeline = timeline[timeline >= first_event]
    if timeline.empty:
        return pd.DataFrame(), missing_tickers

    prices = prices.reindex(timeline).ffill()
    tickers = list(prices.columns)

    position_changes = pd.DataFrame(0.0, index=timeline, columns=tickers)
    cash_changes = pd.Series(0.0, index=timeline, dtype=float)
    external_flows = pd.Series(0.0, index=timeline, dtype=float)

    for _, row in tx.iterrows():
        date = row["Date"]
        if pd.isna(date) or date not in position_changes.index:
            continue

        ticker = str(row["Ticker"])
        quantity = float(row["Quantity"])
        price = float(row["Price"])
        fees = float(row.get("Fees", 0.0))
        signed_quantity = quantity if row["Type"] == "BUY" else -quantity
        position_changes.at[date, ticker] += signed_quantity

        trade_value = quantity * price
        cash_changes.at[date] += (
            -trade_value - fees
            if row["Type"] == "BUY"
            else trade_value - fees
        )

    for _, row in cf.iterrows():
        date = row["Date"]
        if pd.isna(date) or date not in cash_changes.index:
            continue

        amount = float(row["Amount"])
        event_type = str(row["Type"]).upper()

        if event_type in {"DEPOSIT", "DIVIDEND"}:
            cash_changes.at[date] += amount
        elif event_type == "WITHDRAWAL":
            cash_changes.at[date] -= amount

        if event_type == "DEPOSIT":
            external_flows.at[date] += amount
        elif event_type == "WITHDRAWAL":
            external_flows.at[date] -= amount

    positions = position_changes.cumsum()
    cash = float(starting_free_cash) + cash_changes.cumsum()

    signed_values = positions * prices
    missing_open_price = ((positions.abs() > 1e-12) & prices.isna()).any(axis=1)
    market_value = signed_values.fillna(0.0).sum(axis=1)
    equity = cash + market_value
    equity.loc[missing_open_price] = pd.NA

    previous_equity = equity.shift(1)
    daily_return = (equity - previous_equity - external_flows) / previous_equity
    invalid_denominator = previous_equity.isna() | (previous_equity.abs() < 1e-12)
    daily_return.loc[invalid_denominator] = pd.NA

    # The first valued observation establishes the base rather than representing
    # a one-day investment return.
    first_valid_equity = equity.first_valid_index()
    if first_valid_equity is not None:
        daily_return.loc[first_valid_equity] = 0.0

    cumulative_return = (1.0 + daily_return.fillna(0.0)).cumprod() - 1.0
    wealth_index = cumulative_return + 1.0
    peak = wealth_index.cummax()
    drawdown = wealth_index / peak - 1.0

    result = pd.DataFrame({
        "Cash": cash,
        "Signed Market Value": market_value,
        "Equity": equity,
        "External Flow": external_flows,
        "Daily Return": daily_return,
        "Cumulative Return": cumulative_return,
        "Drawdown": drawdown,
    })
    result.index.name = "Date"

    return result, missing_tickers


#______________________________________________________________________________
# VALUE TODAY'S OPEN POSITIONS
#______________________________________________________________________________

def value_current_positions(
    positions: pd.DataFrame,
    latest_prices: pd.Series,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Mark open long and short positions to the latest market price."""
    result = positions.copy()

    if result.empty:
        return result, {
            "Long Exposure": 0.0,
            "Short Exposure": 0.0,
            "Gross Exposure": 0.0,
            "Net Exposure": 0.0,
            "Unrealised P&L": float("nan"),
        }

    result["Current Price"] = result["Ticker"].map(latest_prices)
    result = result.dropna(subset=["Current Price"]).copy()

    result["Signed Market Value"] = (
        result["Quantity"] * result["Current Price"]
    )
    result["Exposure"] = result["Signed Market Value"].abs()

    # Works for both longs and shorts when entry price is known.
    # For a holdings-only snapshot with no cost basis, P&L stays unavailable
    # rather than being invented.
    if "Average Entry Price" in result.columns:
        result["Unrealised P&L"] = (
            result["Quantity"]
            * (result["Current Price"] - result["Average Entry Price"])
        )
    else:
        result["Unrealised P&L"] = float("nan")

    long_exposure = result.loc[
        result["Quantity"] > 0, "Signed Market Value"
    ].sum()

    short_exposure = -result.loc[
        result["Quantity"] < 0, "Signed Market Value"
    ].sum()

    total_unrealised = (
        result["Unrealised P&L"].sum(min_count=1)
        if "Unrealised P&L" in result
        else float("nan")
    )

    return result, {
        "Long Exposure": float(long_exposure),
        "Short Exposure": float(short_exposure),
        "Gross Exposure": float(long_exposure + short_exposure),
        "Net Exposure": float(long_exposure - short_exposure),
        "Unrealised P&L": float(total_unrealised)
        if pd.notna(total_unrealised)
        else float("nan"),
    }


#______________________________________________________________________________
# ACCOUNTING AUDIT BENCHMARK
#______________________________________________________________________________

def build_transaction_price_audit(
    transactions: pd.DataFrame,
    positions: pd.DataFrame,
    cash_balance: float,
    as_of_date: Any = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """
    Value reconstructed positions using each ticker's latest accepted BUY/SELL
    transaction price.

    This is an accounting audit proxy, not a live market valuation.
    """
    tx = prepare_transactions(transactions)

    if as_of_date is not None:
        as_of = pd.Timestamp(as_of_date)
        tx = tx[
            tx["Date"] <= as_of
        ].copy()

    if positions is None or positions.empty or tx.empty:
        exposure = {
            "Long Exposure": 0.0,
            "Short Exposure": 0.0,
            "Gross Exposure": 0.0,
            "Net Exposure": 0.0,
            "Unrealised P&L": float("nan"),
            "Cash Balance": float(cash_balance),
            "Portfolio Equity": float(cash_balance),
        }

        return pd.DataFrame(), exposure

    last_prices = (
        tx.sort_values("Date", kind="stable")
        .groupby("Ticker", sort=False)["Price"]
        .last()
    )

    audited, exposure = value_current_positions(
        positions,
        last_prices,
    )

    signed_market_value = audited[
        "Signed Market Value"
    ].sum()

    exposure["Cash Balance"] = float(
        cash_balance
    )

    exposure["Portfolio Equity"] = float(
        cash_balance + signed_market_value
    )

    return audited, exposure
