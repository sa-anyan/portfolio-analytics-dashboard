"""Offline demo provisioning after the existing budget script. No API access."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio_analytics.security.demo_quota import provision_demo

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Supply the existing absolute budget path with approved demo environment settings.')
    provision_demo(sys.argv[1])
    print('Demo quota provisioned. No provider calls made.')
