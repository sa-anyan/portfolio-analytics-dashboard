# Portfolio Analytics & Risk Platform

A Python and Streamlit application for turning portfolio holdings and
transaction data into a validated, auditable view of positions,
valuation, performance and risk.

The project explores a practical problem in investment analytics:
portfolio analysis is only as reliable as the data and accounting
underneath it. The application therefore combines portfolio-data
ingestion, data-quality checks, transaction accounting, market valuation
and risk analytics in one workflow.

## Key Features

### Portfolio ingestion and data quality

-   Upload CSV and Excel holdings or transaction-ledger files.
-   Detect portfolio structure and map relevant fields.
-   Resolve tickers and review uncertain security matches before
    analysis.
-   Identify missing, inconsistent or contradictory financial data.
-   Generate repair suggestions with supporting evidence and confidence.
-   Preserve an audit trail of accepted, rejected and unresolved
    changes.

### Transaction accounting

-   Reconstruct current long and short positions from transaction
    history.
-   Track free cash and restricted short-sale cash.
-   Calculate realised and unrealised P&L.
-   Reconstruct dated account equity from transaction activity.
-   Keep unresolved transactions out of accounting until reviewed.

### Valuation

-   Live market valuation.
-   Historical as-of valuation.
-   Frozen-price mode for controlled testing.
-   Current market price and daily price movement for open positions.

### Portfolio and risk analytics

-   Annualised return and volatility.
-   Sharpe ratio.
-   Historical Value at Risk (VaR).
-   Expected Shortfall (ES).
-   Maximum drawdown.
-   Correlation analysis.
-   Asset risk contribution.
-   Historical portfolio performance.
-   Stress testing.
-   Monte Carlo portfolio analysis where appropriate.
-   Security-level risk analytics.

## How It Works

``` text
Portfolio file
     |
     v
Schema detection
     |
     v
Data Quality Check
     |
     v
Review / repair uncertain data
     |
     v
Validated portfolio or ledger
     |
     +----------------------+
     |                      |
     v                      v
Accounting engine      Market data
     |                      |
     v                      v
Open positions  --->  Valuation engine
     |                      |
     +----------+-----------+
                |
                v
      Portfolio & risk analytics
```

For transaction ledgers, the system reconstructs the current open book
before running holdings-style analytics. Historical transaction
performance remains separate from analysis of the portfolio's current
open positions.

## Technology

Python, Streamlit, pandas, NumPy, Plotly, yfinance and pytest.

## Project Structure

``` text
portfolio-analytics-dashboard/
├── app.py                         # Streamlit application entry point
├── portfolio_analytics/           # Core application package
│   ├── ingestion/                 # File parsing and schema detection
│   │   ├── input_parser.py
│   │   └── parsing_engine.py
│   ├── quality/                   # Data-quality checks and repair logic
│   │   ├── auto_clean.py
│   │   ├── repair_engine.py
│   │   └── pattern_learning.py
│   ├── accounting/                # Position, cash and valuation engines
│   │   ├── today_engine.py
│   │   └── valuation_engine.py
│   ├── analytics/                 # Market, risk and stress analytics
│   │   ├── market_data.py
│   │   ├── market_analysis.py
│   │   └── stress_test.py
│   └── validation/                # Synthetic validation and stress labs
│       ├── validation_lab.py
│       ├── automated_validation_lab.py
│       ├── mass_validation_lab.py
│       └── hard_validation_lab.py
├── tests/                         # Automated regression and unit tests
├── validation_outputs/            # Validation policy and benchmark outputs
├── docs/                          # Testing and validation documentation
├── .streamlit/                    # Streamlit configuration
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Run Locally

Clone the repository and enter the project directory:

``` bash
git clone https://github.com/sa-anyan/portfolio-analytics-dashboard.git
cd portfolio-analytics-dashboard
```

Create and activate a virtual environment:

``` bash
python -m venv .venv
source .venv/bin/activate
```

On Windows, activate it with:

``` bash
.venv\Scripts\activate
```

Install the dependencies and run the application:

``` bash
pip install -r requirements.txt
streamlit run app.py
```

## Testing

Run the automated test suite with:

``` bash
pytest -q
```

The project also contains validation tooling for testing portfolio-data
cleaning and repair behaviour under synthetic corruption scenarios.

## Design Principles

-   Unresolved data should not silently enter accounting.
-   Cash and positions are reconstructed before portfolio analytics.
-   Market-price files are kept separate from transaction accounting.
-   Historical account performance and current-open-book analytics are
    treated as different questions.
-   Missing market information is displayed as unavailable rather than
    replaced with fabricated values.
-   Suggested data repairs remain reviewable and auditable.

## Data Privacy

Do not commit brokerage statements, personal portfolio exports,
credentials, API keys or Streamlit secrets to the repository. Local
portfolio data and development-environment files should remain excluded
through `.gitignore`.

## Status

This is an actively developed portfolio analytics prototype. The current
repository version is based on **v3.57.5**.
