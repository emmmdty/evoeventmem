---
name: execute-project-task
description: Execute exactly one EvoEventMem task ID from tasks/mainline or tasks/optional. Use when the user names a task ID or asks to continue the project backlog. Do not use for whole-project implementation.
---

1. Read `/AGENTS.md`, `/TASKS.md`, and the selected task file.
2. Confirm prerequisites from the task index; do not begin if an earlier mainline task is incomplete in the repository state.
3. Inspect only the listed context files first. Use `rg` for additional symbols.
4. Produce a short plan tied to acceptance criteria.
5. Make the smallest coherent implementation for this task only.
6. Run every listed verification command. Never omit a failed command from the report.
7. Stop after acceptance. Report changed files, command results, acceptance checklist, and risks.
8. Do not update benchmark claims or resume metrics unless generated run artifacts support them.
