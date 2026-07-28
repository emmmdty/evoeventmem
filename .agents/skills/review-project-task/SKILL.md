---
name: review-project-task
description: Review one EvoEventMem task implementation against its task file and repository rules. Use for a bounded post-implementation review; never expand into implementation.
---

1. Read `AGENTS.md`, the selected task file, and the diff.
2. Check scope, acceptance criteria, test quality, architecture boundaries, provenance/temporal invariants, and benchmark fairness.
3. Run read-only or non-destructive verification commands when available.
4. Return findings ordered by severity with exact files/symbols and reproduction steps.
5. State which acceptance criteria are verified, unverified, or failed.
6. Do not edit files or propose work from later tasks unless it blocks correctness.
