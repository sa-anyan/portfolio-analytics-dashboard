import numpy as np
import pandas as pd

from portfolio_analytics.validation.hard_validation_lab import _apply_corruption, _value_matches, run_hard_validation
from portfolio_analytics.quality.pattern_learning import learn_portfolio_patterns
from portfolio_analytics.quality.repair_engine import apply_repairs, diagnose_repairs


def _clean_fixture():
    return pd.read_csv('tests/fixtures/clean_transactions.csv')


def test_wrong_ticker_is_repaired_from_asset_mapping():
    clean = _clean_fixture()
    profile = learn_portfolio_patterns(clean.iloc[20:].copy(), profile_name='test')
    row = clean.iloc[[0]].copy().reset_index(drop=True)
    row.at[0, 'Ticker'] = 'JMP'
    suggestions, _ = diagnose_repairs(row, reference_profile=profile, learn_from_current=False)
    ticker = suggestions[suggestions['Source Column'].eq('Ticker')]
    assert not ticker.empty
    assert ticker.iloc[0]['Repair Type'] == 'MAPPING_CORRECTION'
    repaired, _ = apply_repairs(row, suggestions, accepted_indices=list(suggestions.index))
    assert repaired.at[0, 'Ticker'] == 'JPM'


def test_numeric_contradiction_is_repaired_or_escalated_not_silently_ignored():
    clean = _clean_fixture()
    profile = learn_portfolio_patterns(clean.iloc[20:].copy(), profile_name='test')
    row = clean.iloc[[0]].copy().reset_index(drop=True)
    row.at[0, 'Gross_Value'] = float(row.at[0, 'Gross_Value']) * 10
    suggestions, diagnostics = diagnose_repairs(row, reference_profile=profile, learn_from_current=False)
    gross = suggestions[suggestions['Source Column'].eq('Gross_Value')] if not suggestions.empty else pd.DataFrame()
    manual = diagnostics.get('manual_questions', pd.DataFrame())
    assert (not gross.empty) or (isinstance(manual, pd.DataFrame) and not manual.empty)


def test_hard_lab_is_not_trivially_perfect():
    clean = _clean_fixture()
    summary, evidence, artefacts = run_hard_validation(clean, scenarios=20, progress_every=1000)
    assert summary['held_out_decisions'] > 0
    assert summary['overall_decision_accuracy'] < 1.0
    assert summary['corruptions_generated'] > summary['scenarios']
    assert 'methods' in artefacts['policy']
