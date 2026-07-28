from __future__ import annotations

import argparse

from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import MemoryService


def smoke() -> None:
    service = MemoryService(InMemoryMemoryRepository())
    service.write(
        MemoryRecord(
            user_id="smoke-user",
            kind=MemoryKind.EVENT,
            content="The project switched the package registry to npmmirror.",
            entities=["project", "npmmirror"],
            evidence=[EvidenceRef(source_type="fixture", source_id="smoke-1")],
        )
    )
    hits = service.search("smoke-user", "Which package registry does the project use?")
    if not hits:
        raise SystemExit("smoke failed: no memory retrieved")
    print(f"smoke ok: {hits[0].memory.content} score={hits[0].score:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="evoeventmem")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("smoke")
    args = parser.parse_args()
    if args.command == "smoke":
        smoke()


if __name__ == "__main__":
    main()
