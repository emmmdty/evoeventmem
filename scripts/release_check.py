from __future__ import annotations

from pathlib import Path

REQUIRED = [
    "README.md",
    "AGENTS.md",
    "TASKS.md",
    "docs/EVALUATION.md",
    "docs/DATASETS.md",
    "adapters/opencode/README.md",
]


def main() -> None:
    missing = [path for path in REQUIRED if not Path(path).exists()]
    if missing:
        raise SystemExit(f"missing release files: {missing}")
    print("release skeleton ok")


if __name__ == "__main__":
    main()
