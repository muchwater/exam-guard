"""Config loading with explicit TBD handling.

Every numeric parameter lives in configs/*.yaml next to a `# source:` comment.
A value that has no source yet is the string "TBD". Code must read such values
through `require()`, which raises `TBDError` naming the key, so a run never
silently uses an invented number.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterator

import yaml

TBD = "TBD"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"


class TBDError(RuntimeError):
    """Raised when code needs a parameter whose value is still TBD."""

    def __init__(self, keys: list[str]):
        self.keys = keys
        super().__init__(
            "parameter(s) still TBD (measure or cite a source, then set them in configs/): "
            + ", ".join(keys)
        )


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(*paths: str | Path) -> dict:
    """Load configs/default.yaml, then merge each given override file in order."""
    cfg: dict = {}
    for p in (DEFAULT_CONFIG, *paths):
        with open(p, encoding="utf-8") as f:
            cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def is_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() == TBD
    if isinstance(value, (list, tuple)):
        return any(is_tbd(v) for v in value)
    if isinstance(value, dict):
        return any(is_tbd(v) for v in value.values())
    return False


def require(cfg: dict, *dotted: str) -> Any:
    """Return the value(s) at the dotted key(s); raise TBDError if any is TBD or missing."""
    missing = [k for k in dotted if get(cfg, k, None) is None]
    if missing:
        raise KeyError(f"config key(s) missing: {', '.join(missing)}")
    bad = [k for k in dotted if is_tbd(get(cfg, k))]
    if bad:
        raise TBDError(bad)
    vals = [get(cfg, k) for k in dotted]
    return vals[0] if len(vals) == 1 else tuple(vals)


def iter_tbd(node: Any, prefix: str = "") -> Iterator[str]:
    """Yield the dotted path of every TBD leaf."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_tbd(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_tbd(v, f"{prefix}[{i}]")
    elif is_tbd(node):
        yield prefix
