#!/usr/bin/env python3
"""Legacy manual utility. Not used by IconMaker runtime."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    print('GenGlobal.py is legacy-only and intentionally not wired into the app runtime.')
    print(f'Location: {Path(__file__).resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
