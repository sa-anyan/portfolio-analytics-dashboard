"""Offline provisioning only: python scripts/provision_ai_budget.py /private-volume/budget.sqlite"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio_analytics.security.ai_access import initialise_budget
if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Supply a new absolute budget database path in a private persistent directory.')
    initialise_budget(sys.argv[1])
    print('Budget provisioned. No credentials configured and no paid calls made.')
