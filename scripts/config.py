"""
config.py - read secrets and settings from .env.

Stdlib only; no python-dotenv. Real OS environment variables win over .env,
so a key can be exported for a one-off run without editing the file.

Import from other scripts:
    from config import get, require
    key = require("THE_ODDS_API_KEY")

Check it directly:
    py -3 scripts/config.py
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    vals: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            # tolerate quoted values
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            vals[k.strip()] = v
    _cache = vals
    return vals


def get(name: str, default: str | None = None) -> str | None:
    """OS environment takes precedence over .env."""
    v = os.environ.get(name)
    if v not in (None, ""):
        return v
    v = _load().get(name)
    return v if v not in (None, "") else default


def require(name: str) -> str:
    v = get(name)
    if not v:
        raise SystemExit(
            f"\nMissing required setting: {name}\n"
            f"  1. Copy-Item .env.example .env\n"
            f"  2. Set {name}=<your key> in {ENV_PATH}\n"
            f"  3. Verify with:  py -3 scripts/config.py\n"
        )
    return v


def masked(v: str) -> str:
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}{'*' * (len(v) - 8)}{v[-4:]}"


def main() -> int:
    print(f".env path : {ENV_PATH}")
    print(f".env found: {ENV_PATH.exists()}")
    if not ENV_PATH.exists():
        print("\n  Not set up yet. Run:")
        print("    Copy-Item .env.example .env")
        print("  then edit .env and put your key after THE_ODDS_API_KEY=")
        return 1

    print()
    ok = True
    checks = [
        ("THE_ODDS_API_KEY", True),
        ("SPORTSGAMEODDS_API_KEY", False),
        ("ODDS_API_REGIONS", False),
        ("ODDS_API_MAX_CREDITS_PER_RUN", False),
    ]
    for name, required in checks:
        v = get(name)
        if v:
            shown = masked(v) if "KEY" in name else v
            src = "env" if os.environ.get(name) else ".env"
            print(f"  OK       {name:<32} {shown}  ({src})")
        elif required:
            print(f"  MISSING  {name:<32} <- required")
            ok = False
        else:
            print(f"  unset    {name:<32} (optional)")

    if ok:
        print("\nReady. Next: the odds snapshot pipeline can be built against this.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
