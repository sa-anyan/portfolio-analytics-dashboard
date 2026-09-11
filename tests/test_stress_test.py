import pandas as pd

from stress_test import run_stress_test


def test_stress_test_preserves_long_short_direction():
    portfolio = pd.DataFrame({
        "Ticker": ["LONG", "SHORT"],
        "Signed Market Value": [100.0, -50.0],
    })
    results, stressed, impact, pct = run_stress_test(
        portfolio,
        {"LONG": -0.10, "SHORT": -0.10},
        value_column="Signed Market Value",
    )
    assert round(float(results.loc[0, "Impact"]), 6) == -10.0
    assert round(float(results.loc[1, "Impact"]), 6) == 5.0
    assert round(impact, 6) == -5.0
    assert round(stressed, 6) == 45.0
    assert round(pct, 6) == -0.1
