"""List every parameter that is still TBD (no source yet).

    python -m tools.list_tbd [--config configs/site.yaml]
"""
from __future__ import annotations

import argparse

from common.config import iter_tbd, load_config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    keys = list(iter_tbd(load_config(*a.config)))
    for k in keys:
        print(k)
    print(f"{len(keys)} TBD parameter(s)")


if __name__ == "__main__":
    main()
