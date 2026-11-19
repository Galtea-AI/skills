"""Runs the test_*.py beside each script under .github/scripts/.

Standard library only: `python3 .github/scripts/run_tests.py` on Python 3.9+.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

suite = unittest.TestSuite()
for folder in sorted(path for path in HERE.iterdir() if path.is_dir() and not path.name.startswith((".", "_"))):
    suite.addTests(unittest.TestLoader().discover(str(folder), top_level_dir=str(folder)))

result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
