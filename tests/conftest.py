from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def clean_transactions() -> pd.DataFrame:
    return pd.read_csv(PROJECT_ROOT / "tests" / "fixtures" / "clean_transactions.csv")
