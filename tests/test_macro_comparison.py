import numpy as np
import pandas as pd
from portfolio_analytics.analytics.macro import monthly_comparison


def test_rates_are_monthly_changes_and_dates_align_without_missing_month_fill():
    dates = pd.bdate_range('2024-01-01', '2026-01-30')
    returns = pd.Series(.001 * np.sin(np.arange(len(dates))), index=dates)
    rate_dates = pd.date_range('2023-12-01', '2026-01-01', freq='MS')
    rates = pd.Series(np.arange(len(rate_dates)) ** 2 / 100, index=rate_dates)
    aligned, evidence = monthly_comparison(returns, rates)
    assert evidence['available']
    assert evidence['observations'] == 23
    assert evidence['start'] == '2024-02'
    assert evidence['end'] == '2025-12'
    assert aligned.iloc[0].rate_change_pp == rates.loc['2024-02-01'] - rates.loc['2024-01-01']
    rates = rates.drop(pd.Timestamp('2024-08-01'))
    aligned, evidence = monthly_comparison(returns, rates)
    assert evidence['observations'] == 21  # missing level and following month change excluded


def test_short_history_and_constant_rates_do_not_report_correlation_zero():
    dates = pd.bdate_range('2024-01-01', '2024-05-31')
    rates = pd.Series(5., index=pd.date_range('2024-01-01', '2024-05-01', freq='MS'))
    _, summary = monthly_comparison(pd.Series(.001, index=dates), rates)
    assert not summary['available']
    assert summary['correlation'] is None
