# Portfolio calculation conventions

All dashboard values and scenario execution prices are in USD. Inputs use local quote prices and quantities; `Currency` must match the listing's quote units (`GBX` is GBP pence). Missing currency defaults to USD and is disclosed. Ticker/listing and currency must be checked against the broker record before presenting results.

## Valuation and accounting

- Local current marks are translated using current FX. Dated cost basis and ledger executions/fees are translated at event-date FX. Undated foreign cost basis uses current FX and carries an approximation warning.
- Cash is kept as balances in each currency and revalued at current/historical FX. Deposits, withdrawals and dividends use their supplied currency. The starting-cash input is USD. FX cash exchanges must be represented by both currency cash legs.
- Average-cost realised/unrealised security P&L is gross of fees. Fees reduce account cash and account performance. Cash FX changes are account effects, separate from security P&L. Unknown or partially unknown cost basis remains unknown; mixed-sign snapshot lots are rejected.
- Incomplete current price/FX coverage or invalid input rows prevent a complete portfolio valuation. Missing full-portfolio return coverage withholds headline risk rather than treating an absent asset as zero-return cash.

## Risk and simulated performance

Risk uses adjusted-close total-return ratios translated into base currency. `1+r_USD = (1+r_local)(1+r_FX)`. Foreign cash contributes its FX return; base cash earns zero. Signed market values divided by equity supply weights. Headline risk requires positive equity and at least two common return observations across every active security/currency balance.

The calendar contains business-day observations, with annualisation factor 252. Crypto weekend moves are included in the next business-day price change. This is a common weekday convention, not a claim that every venue has 252 sessions. Align venue calendars and investigate stale/missing marks for production use.

`annual_return` is arithmetic annualised mean (`252 × mean daily return`), not CAGR. `geometric_annual_return` chains returns and annualises over the observed daily sample. Sharpe converts the supplied effective annual risk-free rate to a daily equivalent; the app currently defaults to zero. Square-root annualisation is a conventional estimate, not a serial-correlation adjustment.

Historical VaR is the negative empirical lower quantile, floored at zero. Expected Shortfall is the negative mean of returns at or below that cutoff, also floored at zero. The quantile uses pandas linear interpolation. Tail ties are included. Horizon is one business-day observation. Confidence, common sample size and tail observation count are reported; small tails provide weak estimates.

Compounding returns at today's fixed signed weights is a hypothetical constant-weight simulation with implied daily rebalancing. It is not actual realised performance or buy-and-hold history. Funding costs, borrow fees and cash interest are excluded. Euler volatility contributions sum to volatility; the donut displays absolute normalised contributions and the adjacent table retains signed hedge effects.

Every drawdown includes the initial wealth baseline of one. If a hypothetical daily loss wipes out equity, compounded growth/drawdown is withheld rather than chaining through negative wealth. Equal-weight security combinations use a shared date window across the selected universe, so their rankings compare the same historical period.

## Dated account and holdings history

The app downloads separate prices for accounting. Dividend-adjusted total-return levels are rejected for account marks.

- **Transaction ledger:** Yahoo Close is retrospectively split-adjusted. The adapter restores contemporaneous price levels using subsequent split ratios; ledger quantity and average basis change on each split date before trades. Explicit executions, fees and dividend cash flows are replayed once. Dividends absent from the supplied ledger are not inferred. Unsupported ledger event types are rejected rather than ignored.
- **Dated holdings snapshot:** today's quantities are interpreted as current split-adjusted shares, multiplied by split-adjusted close. Supplied average cost must be on that same current-share basis; original execution-unit data belongs in a ledger. Each lot enters at its first market mark on/after its purchase date, with that mark removed as an external capital addition. Duplicate lots are accumulated. This is a price-only reconstruction of current holdings; sold holdings, historical dividends and cash flows cannot be recovered from a snapshot. Supplied currency cash balances, including starting cash, are held constant in native units and revalued through FX.

Full history is withheld if dated lots, required prices or FX are missing. All ledger tickers, including closed positions, and all cash-flow currencies are included. A ledger with no security trades has no reconstructed security account path in this version.

Daily account return uses an **end-of-day external-flow convention**:

`r_t = (V_t - V_(t-1) - F_t) / V_(t-1)`.

Weekend events are booked at the next business-day valuation. This cannot recreate intraday cash-flow timing; beginning-of-day or large intraday flows require finer valuations or a separately defined timing method. Initial capital is the first return denominator where positive. If the account begins with no capital, the first funded valuation establishes a baseline and is excluded from return statistics; same-day performance before that first valuation cannot be measured. Account gain is ending equity minus opening capital minus net external contributions.

## Attribution

Position P&L (excluding capital additions) is divided by prior total account equity, including cash. Daily contributions are multiplied by wealth accumulated before that day; their linked sum therefore reconciles to the compounded selected-interval return. Cash FX, unassigned dividend income and other account effects appear as an explicit residual. `reconciliation_error_pct_points` measures the difference after including that residual.

Returns start at the first selected valuation, excluding changes before the requested window. Drawdown attribution uses its peak-to-trough interval; the full requested-window return is a separate field. Attribution describes arithmetic contributions and does not infer fundamental/news causes.

## Reproducibility and recovery

The pre-fix version is Git commit `4a254a4d981e7f6779d397b0b7063fe7989b0380`. GitHub commit history preserves it, so no separate copy is needed. Use a checkout/worktree at that commit to inspect the old version; to undo deployed changes, revert the fix commit(s) through a new commit rather than rewriting shared history.

The regression suite includes numerical examples for currencies/pence, initial loss drawdown, duplicate lots, attribution with cash and compounding, opening capital, missing asset/FX coverage, dividends and splits. Before a presentation, freeze the portfolio data/as-of date and reconcile current and reconstructed ending equity against the broker record. Test success validates implementation behaviour; it does not establish that user-entered metadata or provider prices are correct.
