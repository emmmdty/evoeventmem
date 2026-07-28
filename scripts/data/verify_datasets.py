from __future__ import annotations

import argparse
from pathlib import Path

from _download import load_manifest, validate_json

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/manifests/datasets.json"

DESTINATIONS = {
    "longmemeval-cleaned": ROOT / "data/raw/longmemeval",
    "locomo": ROOT / "data/raw/locomo",
}

DOWNLOAD_COMMANDS = {
    ("longmemeval-cleaned", "oracle"): "python scripts/data/download_longmemeval.py --variant oracle",
    ("longmemeval-cleaned", "s"): "python scripts/data/download_longmemeval.py --variant s",
    ("longmemeval-cleaned", "m"): "python scripts/data/download_longmemeval.py --variant m",
    ("locomo", "locomo10"): "python scripts/data/download_locomo.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(MANIFEST)
    missing: list[Path] = []
    for dataset in manifest.datasets:
        if dataset.id not in DESTINATIONS:
            if dataset.role == "optional":
                print(
                    f"optional: {dataset.id} is not required for mainline verification; "
                    "see docs/DATASETS.md before selecting its optional task",
                )
            continue
        for file in dataset.files:
            path = DESTINATIONS[dataset.id] / file.filename
            if not path.exists():
                print(f"missing: {path}")
                command = DOWNLOAD_COMMANDS.get((dataset.id, file.variant))
                if command is not None:
                    print(f"  download: {command}")
                missing.append(path)
                continue
            result = validate_json(
                path,
                expected_shape=file.expected_shape,
                expected_records=file.expected_records,
                expected_size_bytes=file.expected_size_bytes,
                sha256=file.sha256,
            )
            print(
                f"ok: {path} shape={result.shape} "
                f"records={result.record_count} bytes={result.size_bytes}",
            )
    if missing and not args.allow_missing:
        raise SystemExit(f"{len(missing)} expected files are missing")


if __name__ == "__main__":
    main()
