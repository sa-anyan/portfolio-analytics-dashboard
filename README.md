# Portfolio Analytics

A Streamlit portfolio analytics prototype for ingesting holdings or transaction-ledger data, validating and repairing financial inputs, reconstructing current positions and cash, valuing the portfolio, and running portfolio/risk analytics.

## What the app does

- Upload CSV/XLSX holdings, transaction ledgers, and supported hybrid exports.
- Detect portfolio schemas and review uncertain security/ticker matches.
- Run a Data Quality Check with transparent repair suggestions and audit information.
- Reconstruct long/short open positions, free cash, restricted short-sale cash, realised P&L and account equity from transaction data.
- Value positions using live, historical-as-of, or frozen-test pricing modes.
- Analyse the current open book with return, volatility, Sharpe ratio, VaR, Expected Shortfall, drawdown, correlation and risk contribution.
- Show current market exposure, current price and day change for resolved open positions.
- Run stress tests and Monte Carlo analysis where appropriate.

## Project structure

```text
portfolio_analytics/
├── app.py                    # Streamlit application and UI orchestration
├── input_parser.py           # File ingestion and portfolio/ledger preparation
├── parsing_engine.py         # Schema detection and semantic column matching
├── auto_clean.py             # Cleaning pipeline
├── repair_engine.py          # Repair diagnosis and suggestions
├── pattern_learning.py       # Deterministic/statistical dataset pattern learning
├── today_engine.py           # Position, cash and historical equity accounting
├── valuation_engine.py       # Live / historical / frozen valuation
├── market_data.py            # Market-data retrieval and preparation
├── market_analysis.py        # Portfolio and security analytics
├── stress_test.py            # Scenario stress testing
├── validation_lab.py         # Validation utilities
├── automated_validation_lab.py
├── mass_validation_lab.py
├── hard_validation_lab.py
├── validation_outputs/       # Version-controlled validation policy/results
├── tests/                    # Automated test suite
├── docs/
│   ├── releases/             # Historical version notes
│   └── validation/           # Validation run reports
├── .streamlit/config.toml    # Streamlit theme/configuration
├── requirements.txt
└── .gitignore
```

## Run locally

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\\Scripts\\activate      # Windows
pip install -r requirements.txt
```

Start the app:

```bash
streamlit run app.py
```

## Tests

```bash
pytest -q
```

## Data and privacy

Do not commit personal portfolio files, brokerage exports, API keys, `.streamlit/secrets.toml`, or your virtual environment. The included `.gitignore` excludes common local data and secret files while preserving the validation outputs used by the prototype.

## Current version

Clean repository layout based on v3.57.5. Historical implementation notes are kept under `docs/releases/` rather than cluttering the repository root.
