# Portfolio Analytics v4 Architecture

## Core rule

**AI interprets. Python validates and calculates. AI explains.**

There is one accepted portfolio state and one calculation path for each metric.
`app.py` contains presentation logic only.

## Data flow

```text
                         USER
                          │
          ┌───────────────┴────────────────┐
          │                                │
     Portfolio input                  AI question
          │                                │
          ▼                                ▼
┌──────────────────┐             ┌───────────────────┐
│   INPUT ENGINE   │             │   AI INTERPRETER  │
│ CSV / Excel      │             │ What does user    │
│ Manual entry     │             │ actually want?    │
│ Transaction log  │             └─────────┬─────────┘
└────────┬─────────┘                       │
         │                                 │
    ┌────┴──────────────┐                  │
    │                   │                  │
 FILE INPUT        MANUAL ENTRY            │
    │                   │                  │
    ▼                   │                  │
┌──────────────────┐    │                  │
│ PARSER + DETECTOR│    │                  │
│ holdings/ledger  │    │                  │
└────────┬─────────┘    │                  │
         │              │                  │
    ┌────┴─────┐        │                  │
    ▼          ▼        │                  │
HOLDINGS     LEDGER     │                  │
 PATH         PATH      │                  │
    │          │        │                  │
    └────┬─────┘        │                  │
         └───────┬──────┘                  │
                 ▼                         │
          ┌──────────────┐                 │
          │  PORTFOLIO   │◄────────────────┤
          │    STATE     │                 │
          └──────┬───────┘                 │
                 ▼                         │
          ┌──────────────┐                 │
          │  ANALYTICS   │◄────────────────┤
          └──────┬───────┘                 │
                 ▼                         │
          ┌──────────────┐                 │
          │   SCENARIO   │◄────────────────┤
          │    ENGINE    │                 │
          └──────┬───────┘                 │
                 └────────────┬────────────┘
                              ▼
                      ┌───────────────┐
                      │ AI EXPLAINER  │
                      └───────┬───────┘
                              ▼
                           USER
```

## 1. Input Engine

`portfolio_analytics/input_engine/`

- `parser.py` reads CSV/XLSX and classifies the file during parsing.
- `normalizer.py` maps source columns into canonical holdings or ledger schemas.
- `manual.py` sends manual holdings through the same holdings normaliser.
- Ledger trade quantity is canonicalised as **BUY positive / SELL negative** in `Signed Quantity`.
- Both the original user dataset and the normalised dataset are retained in the parser dictionary.

## 2. Portfolio State

`portfolio_analytics/core/portfolio_state.py`

Portfolio State is the single accounting authority. It derives:

- positions and signed quantity
- current price
- long/short side
- average entry price
- signed market value
- cost basis
- realised/unrealised P&L
- cash and external flows
- equity
- long, short, gross and net exposure
- leverage

For ledgers it replays the normalised transactions using average-cost accounting.

## 3. Analytics

`portfolio_analytics/analytics/engine.py`

Receives Portfolio State plus historical prices and calculates:

- current signed-book historical returns
- annual return and volatility
- historical VaR and Expected Shortfall
- dollar VaR / ES
- Sharpe ratio
- maximum drawdown
- correlation
- risk contribution
- holdings/exposure mix
- ledger-path performance when a ledger is available

The analytics dictionary contains a small public result layer plus serialisable engine data used by scenarios.

## 4. Scenario Engine

`portfolio_analytics/scenarios/engine.py`

Scenario actions operate on deep copies. They never mutate the accepted portfolio.

Supported actions:

- price shock
- interest-rate shock
- resize/remove position
- reallocation
- target annual volatility

Actions are composable and execute sequentially. A later action therefore consumes the output from the previous action.

## 5. AI Copilot

`portfolio_analytics/ai/router.py` + `portfolio_analytics/ai/copilot.py`

`router.py` owns the language-to-JSON contract and Python validation boundary. `copilot.py` builds the privacy-safe engine context, calls the router, dispatches the validated instruction to deterministic tools and explains the returned result. Copilot receives context from every stage through `build_copilot_context()`.

- Lookups query deterministic state.
- Explanations use deterministic results.
- Scenarios are converted to structured actions and passed to the scenario engine.
- General educational questions may use textbook knowledge, but portfolio-specific numbers must come from the engine dictionaries.

Large raw time series are compressed before being sent to the model to control context size. The complete dictionaries remain available inside Python.

## 6. App

`app.py` contains input controls, state review, visual layout and the Copilot panel. It performs no portfolio mathematics.

## Copilot privacy boundary

The full parser and state dictionaries remain available to deterministic Python, but the external LLM receives a privacy-safe context view. Raw uploaded rows, transaction/account identifiers and large return histories are not sent. Copilot receives canonical positions, parser metadata, normalised engine values and calculated results needed to answer portfolio questions.
