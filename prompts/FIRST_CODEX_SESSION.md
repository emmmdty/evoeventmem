# First Codex session

Paste the following into a new Codex chat after opening the repository:

```text
Read AGENTS.md, README.md, TASKS.md, and tasks/mainline/M00_bootstrap.md.
Do not implement M01 or any later task. Verify the starter repository only:
1. inspect the architecture boundaries and task workflow;
2. run the existing tests and CLI smoke command;
3. identify any bootstrap defect that would block M01;
4. fix only confirmed bootstrap defects;
5. report changed files, exact command results, and whether M00 remains accepted.
Use the reviewer subagent after any change.
```

After M00 is verified, start a new chat for M01:

```bash
python scripts/taskctl.py prompt M01
```
