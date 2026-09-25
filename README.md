# Portfolio Analytics

A clean rebuild of the portfolio analytics dashboard around one deterministic portfolio state and an AI Copilot connected to every analytical stage.

## What it does

- CSV/XLSX portfolio upload
- Manual holdings entry
- Automatic holdings vs transaction-ledger detection during parsing
- Canonical normalisation before accounting
- Long/short ledger accounting with signed trade quantities
- Average-cost realised/unrealised P&L
- Cash, equity, cost basis and exposure state
- Live prices and historical data through Yahoo Finance
- Volatility, VaR, Expected Shortfall, Sharpe, drawdown and correlation
- Risk-contribution and holdings/exposure pie charts
- Actual ledger-path performance when enough historical data exists
- Composable price, rate, resize, reallocation and target-volatility scenarios
- OpenAI Copilot router for natural-language lookups, explanations and validated what-if routing
- Side-by-side interactive Plotly visuals with short Copilot captions

## Architecture

See `docs/ARCHITECTURE.md`.

The critical rule is:

> AI interprets → Python validates/calculates → AI explains.

`app.py` contains no financial mathematics.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

For tests:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## OpenAI key

Copy:

```text
.streamlit/secrets.toml.example
```

to:

```text
.streamlit/secrets.toml
```

and add your API key. The real secrets file is ignored by Git.

## Run

```bash
python3 -m streamlit run app.py
```

## Important scenario limits

Rate shocks require duration or explicit rate sensitivity. The system deliberately does not invent equity/crypto responses to interest rates.

The first target-risk method means target **annualised volatility** and proportionally scales the current risky book. It is not yet a full risk-budget optimiser.

Historical risk statistics and scenario outputs are model results, not forecasts or personalised investment advice.


## Copilot Router

The AI router has a strict Python validation boundary before any deterministic portfolio function can execute. It normalises ticker aliases, restricts lookup fields, validates scenario actions against the accepted portfolio, preserves compound action order and separates routing from execution. See `docs/COPILOT_ROUTER.md`.


## Persistent Chat

Conversational context is supported without changing the Portfolio State, analytics, or scenario engines. Copilot can distinguish the accepted portfolio from the latest hypothetical scenario, continue a scenario when the user clearly asks to do so, and answer follow-up lookups against the latest scenario. The accepted portfolio remains immutable.

## Analytics UX

The analytics interface keeps Portfolio State, analytics, scenarios, routing and Copilot execution separate while making the dashboard easier to scan:

- compact side-by-side analytics charts
- holdings and risk-contribution visuals with explanations directly underneath
- a larger correlation grid beside portfolio exposure
- grouped risk commentary for volatility and historical VaR
- explicit accepted-portfolio labelling
- visually separated hypothetical scenario results
- accepted-vs-scenario and position-level comparison charts derived only from scenario-engine output
- scenario tables moved into expanders to reduce vertical length

No financial calculation is performed in the UI visual builders.

## Historical Combination Risk

The analytics engine includes deterministic historical combination-risk analysis.

- Every portfolio ticker is considered for historical-data eligibility.
- Eligible tickers are selected by default; unavailable tickers are explicitly reported.
- The analyst can narrow the universe without changing the accepted Portfolio State.
- Combinations use equal weights so the analysis isolates asset grouping from weight optimisation.
- Metrics: annual volatility, maximum drawdown, historical VaR, Expected Shortfall and annual return context.
- Pair analysis renders as a risk heatmap; larger groups render as ranked combinations.
- Copilot receives the deterministic `combination_risk` dictionary and explains it; the LLM does not calculate risk.
- A 100,000-combination safety limit prevents accidental combinatorial explosions. The UI asks the analyst to reduce the selected universe or group size rather than silently sampling combinations.
- Historical combination results are descriptive, not forecasts.

## Cash Handling

`CASH` is now a reserved non-market asset. It is excluded from Yahoo Finance latest-price and history requests and is valued at 1.0 per unit in its stated currency. A market quote for a security whose symbol is `CASH` can no longer override an explicit cash holding. Regression tests cover both market-data filtering and Portfolio State valuation.

## Dated Portfolio Performance

Dated holdings now preserve purchase dates and purchase prices during normalisation. Portfolio State converts those supplied acquisitions into accounting events, while transaction-ledger uploads continue to use their explicit executions and cash flows. The analytics engine can reconstruct the dated account path using the same deterministic equity-curve logic. Historical Behaviour remains the current-holdings simulation by default; when dated accounting information exists, **View your actual portfolio performance** switches the existing growth and drawdown charts to the reconstructed dated path. A holdings snapshot is labelled as reconstructed because it cannot reveal previously sold positions or unsupplied historical cash flows.

### Accounting-led dated performance
Dated holdings are reconstructed through the accounting path: each current position enters on its supplied purchase date at its supplied purchase price, inferred acquisition funding is recorded as an external contribution, and cash-flow-adjusted returns prevent new capital from being mistaken for investment performance. Transaction ledgers continue to use supplied execution prices and dated cash flows. The existing historical simulation remains the default and the UI toggle switches the existing growth/drawdown charts to the reconstructed account path.

### Dated holdings reconstruction

For a holdings snapshot with purchase dates, the actual-performance view is a reconstruction of the **currently held positions**, not a synthetic transaction ledger. A position is absent before its supplied purchase date and is valued thereafter using historical market prices and historical FX converted to USD. The supplied purchase price remains cost-basis information. On the entry date, the position's first market mark is treated as an external capital addition for return chaining, preventing a difference between execution price and daily close from becoming a fake investment return. The reconstructed value chart shows account market value; return, volatility, Sharpe and drawdown use the separate cash-flow-adjusted performance series. Previously sold positions and unknown historical cash movements cannot be inferred from a holdings snapshot.


## Design Decisions, Assumptions & Limitations

Real portfolio files are rarely standardised. They can differ in column names, date formats, currencies, ticker conventions, price fields and whether they represent current holdings or a transaction history. The application therefore normalises common structures into one Portfolio State before running any analytics.

A core design principle is:

> When information cannot be reconstructed from the supplied portfolio, the system exposes the limitation rather than manufacturing historical information.

### Holdings snapshots are not transaction histories

A holdings snapshot tells the system what the investor currently owns. Purchase dates and purchase prices provide additional accounting context, but they do not reveal the complete history of the account.

Unless explicitly supplied, the system cannot determine:

- positions that were previously bought and fully sold
- historical dividends or other distributions
- deposits and withdrawals
- trading fees, taxes or other charges
- historical changes in the cash balance
- stock splits or other corporate actions not represented in the data
- transactions that occurred before the current holdings were acquired

For this reason, dated-holdings performance is labelled as a **reconstruction of the currently held positions**, rather than a complete historical account record.

A transaction ledger can provide a more complete reconstruction because BUY, SELL, DIVIDEND, TRANSFER and other supplied cash-flow events can be processed explicitly.

### Portfolio value vs investment performance

These are deliberately treated as different concepts.

**Reconstructed Portfolio Value** shows the market value of the supplied positions as they enter and move through time.

**Investment Performance** measures how invested capital performed while separating the mechanical effect of adding new capital.

Position entry is therefore treated as an external capital addition when chaining returns. This prevents a new purchase from appearing as investment performance simply because portfolio value increased.

### Purchase prices and historical market prices

The supplied purchase price is retained as accounting and cost-basis information.

Historical portfolio valuation uses available daily market prices. Because daily data generally represents market closes, the historical close on a purchase date may differ from the investor's actual execution price. The system does not treat that difference as an investment gain or loss on entry.

The reconstruction operates at daily resolution and does not attempt to recreate intraday portfolio movements.

### Cash

Explicit cash is separated from securities so it does not create artificial market exposure.

For a holdings snapshot, today's cash balance does not reveal when that cash entered the portfolio or how it changed historically. The system therefore cannot infer historical deposits, withdrawals or cash movements that are absent from the supplied data.

A transaction ledger containing dated cash flows provides a stronger basis for historical cash reconstruction.

### Currency and FX

Portfolio reporting uses USD as the analytical base currency.

Securities may trade in USD, GBP, EUR or other quote currencies. Historical reconstruction therefore uses available currency information and FX data when converting market values into the common reporting currency.

Where currency information is missing or ambiguous, the application should surface or document the assumption rather than imply that the original file contained information it did not provide.

Ticker and listing ambiguity can also affect currency interpretation because the same company may trade through different listings.

### Historical analytics

Historical volatility, VaR, Expected Shortfall, drawdown, correlation, Combination Risk and historical attribution describe behaviour observed in the available historical data.

They are not forecasts of future returns or losses.

Historical attribution is calculated deterministically from the reconstructed portfolio data available to the analytics engine. Its interpretation is therefore subject to the same holdings-history, cash-flow and data-availability limitations described above.

### AI Copilot

The Copilot does not independently calculate portfolio metrics.

Its role is to interpret the user's question, route validated requests to deterministic Python functions and explain the resulting calculations:

> AI interprets → Python validates/calculates → AI explains.

Portfolio valuation, P&L, exposure, risk, scenarios and historical analytics remain reproducible Python calculations rather than LLM-generated estimates.
