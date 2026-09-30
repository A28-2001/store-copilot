import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.generate import ensure_db  # noqa: E402

ensure_db()  # the database is git-ignored and built on first run
