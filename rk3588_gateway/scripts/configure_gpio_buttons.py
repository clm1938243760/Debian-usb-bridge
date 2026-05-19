#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml


BUTTONS = [
    ("up", 83),
    ("down", 62),
    ("ok", 63),
    ("back", 478),
]


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/rk3588_gateway/config.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    raw["gpio"] = {
        "enabled": True,
        "consumer": "rk3588-gateway",
        "lines": [
            {
                "name": name,
                "enabled": True,
                "backend": "sysfs",
                "chip": "/dev/gpiochip0",
                "line": number,
                "number": number,
                "direction": "in",
                "active_low": True,
                "default": 0,
            }
            for name, number in BUTTONS
        ],
    }

    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"configured gpio buttons in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
