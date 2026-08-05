from __future__ import annotations

import json
from pathlib import Path

from evoeventmem.api.app import app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "api" / "openapi.json"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
