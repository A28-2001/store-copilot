"""Store configuration helpers. Stores live in config/stores.yaml, not in code."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "stores.yaml"
DB_PATH = ROOT / "data" / "store.db"
DOCS_DIR = ROOT / "docs"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def stores() -> list[dict]:
    return load_config()["stores"]


def store_ids() -> list[str]:
    return [s["store_id"] for s in stores()]


def store_by_id(store_id: str) -> dict:
    return next(s for s in stores() if s["store_id"] == store_id)


def store_label(store_id: str) -> str:
    """'Flagship (S1)' style label used everywhere in answers."""
    return f"{store_by_id(store_id)['name']} ({store_id})"


def days_of_history(store_id: str) -> int:
    cfg = load_config()
    end = _as_date(cfg["data_end_date"])
    start = end - dt.timedelta(days=cfg["history_days"] - 1)
    opened = max(_as_date(store_by_id(store_id)["open_date"]), start)
    return (end - opened).days + 1


def low_history_stores(scope: list[str]) -> list[str]:
    limit = load_config()["low_history_days"]
    return [s for s in scope if days_of_history(s) < limit]


def newest_store() -> str:
    return max(stores(), key=lambda s: _as_date(s["open_date"]))["store_id"]


def flagship() -> str:
    return stores()[0]["store_id"]


def _as_date(value) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
