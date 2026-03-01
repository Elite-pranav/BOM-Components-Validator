"""
Deprecated — API is now part of main.py.

Run: python main.py (from the backend/ directory)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __name__ == "__main__":
    print("API has moved to main.py. Run: python main.py")
