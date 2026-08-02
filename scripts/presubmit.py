#!/usr/bin/env python3
"""Portable entrypoint for the V16 one-command presubmit."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOV = ROOT / "gov"
if str(GOV) not in sys.path:
    sys.path.insert(0, str(GOV))

try:
    from zgov.presubmit import main
except ImportError as exc:
    print(
        f"presubmit: cannot import zgov.presubmit from {GOV}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(2)

if __name__ == "__main__":
    raise SystemExit(main())
