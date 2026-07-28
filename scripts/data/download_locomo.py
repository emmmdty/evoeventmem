from __future__ import annotations

import argparse
from pathlib import Path

from _download import download_json, load_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/manifests/datasets.json"
DEST = ROOT / "data/raw/locomo"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dataset = load_manifest(MANIFEST).dataset("locomo")
    item = dataset.files[0]
    download_json(
        item.url,
        DEST / item.filename,
        force=args.force,
        expected_shape=item.expected_shape,
        expected_records=item.expected_records,
        expected_size_bytes=item.expected_size_bytes,
        sha256=item.sha256,
    )


if __name__ == "__main__":
    main()
