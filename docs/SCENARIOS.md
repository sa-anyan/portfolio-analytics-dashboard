# Scenario Engine — v4.1

The scenario engine is deterministic. It never mutates the accepted Portfolio State.
Every scenario action receives a state dictionary and returns a new state plus an audit log.

## Supported actions

- `price_shock` — changes one current market price by a percentage.
- `rate_shock` — applies only where a position has `duration` or `rate_sensitivity`.
- `resize` — changes an existing position by percentage, target quantity, or target exposure.
- `remove_position` — closes an existing position at the current scenario mark.
- `reallocation` — reduces source exposure and transfers the same notional into a target position.
- `target_risk` — proportionally scales the existing risky book toward a target annual volatility.

## Composition

Actions execute in the supplied order. Later actions consume the state created by earlier actions.
For example, if BTC is shocked down 25% and then the short is halved, the hypothetical cover occurs at the already-shocked BTC price.

## Accounting rules for hypothetical trades

Scenario trades are executed at the current scenario mark with no invented fees or slippage.
They update:

- quantity
- cash
- average entry price
- realised P&L
- unrealised P&L
- cost basis
- long/short exposure
- equity and leverage

Reducing or closing a long realises `quantity * (scenario price - average entry)`.
Reducing or closing a short realises `quantity * (average entry - scenario price)` for the quantity covered.
Reversing a position realises the closed side and opens the excess opposite side at the scenario execution price.

## Rate shocks

Rate shocks do not invent sensitivities for equities, crypto, or other assets.
The engine uses:

1. user-supplied `rate_sensitivity`, interpreted as percentage price change for a +100 bp rate move; otherwise
2. modified duration using `ΔP/P ≈ -D × Δy`.

Positions without a supported rate model are left unchanged and reported in the action log.

## Scenario risk metrics

After all actions are executed, the engine recalculates current-book risk using the same historical asset-return matrix already stored by the analytics engine.
This is a **static historical reweighting of the scenario end-state exposures**, not a forecast of future returns.

## Safety and auditability

- The accepted base state is deep-copied before execution.
- Every action is validated before the first action runs.
- Unsupported or malformed actions fail before execution.
- Scenario output includes action-by-action before/after snapshots.
- Position-level before/after comparisons are returned for Copilot and future UI displays.
