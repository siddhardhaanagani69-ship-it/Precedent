"""Run the two real demo decisions, answering the middle choice when needed."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    'Should I leave my stable job for a startup offer?',
    'Should I buy a discounted house in a Houston floodplain or keep renting?',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['startup', 'housing'], help='Run just one example')
    args = parser.parse_args()
    questions = QUESTIONS if not args.only else [QUESTIONS[0 if args.only == 'startup' else 1]]
    for question in questions:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_e2e.py'), question,
                                 '--answer', 'middle', '--featured'], cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
