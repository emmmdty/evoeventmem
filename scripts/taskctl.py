from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "tasks/index.json"


def load_tasks() -> list[dict[str, object]]:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    return [*data["mainline"], *data["optional"]]


def get_task(task_id: str) -> dict[str, object]:
    for task in load_tasks():
        if task["id"] == task_id.upper():
            return task
    raise SystemExit(f"unknown task: {task_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect one bounded EvoEventMem task")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("show", "prompt"):
        p = sub.add_parser(name)
        p.add_argument("task_id")
    args = parser.parse_args()

    if args.command == "list":
        for task in load_tasks():
            print(f"{task['id']:>3}  {task['status']:<5}  {task['title']}")
        return

    task = get_task(args.task_id)
    task_path = ROOT / str(task["path"])
    if args.command == "show":
        print(task_path.read_text(encoding="utf-8"))
        return

    print(
        f"Execute only task {task['id']}: {task['title']}.\n"
        f"Read AGENTS.md, TASKS.md, and {task['path']} first.\n"
        "Start with a concise plan tied to the acceptance criteria. "
        "Do not begin any later task. Run every verification command and stop after acceptance."
    )


if __name__ == "__main__":
    main()
