"""Load parameterized SQL from ``app/config/sql_queries/*.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

_QUERIES_DIR = Path(__file__).resolve().parents[1] / "config" / "sql_queries"


@lru_cache(maxsize=1)
def load_sql_queries() -> Dict[str, Any]:
    """Return the full SQL query catalog merged from category YAML files (cached)."""
    if not _QUERIES_DIR.is_dir():
        raise FileNotFoundError(f"SQL queries directory not found: {_QUERIES_DIR}")

    catalog: Dict[str, Any] = {}
    for path in sorted(_QUERIES_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            continue
        if not isinstance(data, dict):
            raise ValueError(f"Expected mapping in {path}, got {type(data)!r}")
        section = path.stem
        if section in catalog:
            raise ValueError(f"Duplicate SQL query section: {section!r}")
        catalog[section] = data
    return catalog


def get_sql(path: str) -> str:
    """
    Resolve a dotted query path to a SQL string.

    Example: ``get_sql("configuration.fetch_states")``
    """
    node: Any = load_sql_queries()
    parts = path.split(".")
    for part in parts:
        if not isinstance(node, Mapping) or part not in node:
            raise KeyError(f"SQL query not found: {path!r} (missing {part!r})")
        node = node[part]
    if not isinstance(node, str):
        raise TypeError(f"SQL query at {path!r} must be a string, got {type(node)!r}")
    return node.strip()


def list_sql(prefix: str | None = None) -> list[str]:
    """List dotted query paths, optionally under a section prefix."""

    def _walk(node: Any, parts: list[str]) -> list[str]:
        if isinstance(node, str):
            return [".".join(parts)] if parts else []
        if not isinstance(node, Mapping):
            return []
        out: list[str] = []
        for key, value in node.items():
            out.extend(_walk(value, parts + [str(key)]))
        return out

    root: Any = load_sql_queries()
    if prefix:
        for part in prefix.split("."):
            if not isinstance(root, Mapping) or part not in root:
                return []
            root = root[part]
        return _walk(root, prefix.split("."))
    return _walk(root, [])
