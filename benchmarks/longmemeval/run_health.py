"""S8 Step 5 run-health watchdog: auto-restart + failure alerting.

The tunnel watchdog (``tunnel_watchdog.py``) keeps the embedding SSH
tunnel alive. This run-health watchdog keeps the **benchmark process
itself** alive and surfaces failures so the 16-hour stratified100 run
does not die silently mid-way.

Responsibilities:
1. **Process liveness**: every ``--interval`` seconds, check if the run
   PID is alive. If dead:
   - If ``finalized/FINALIZED.json`` exists → run completed, write a
     ``COMPLETED`` status, exit.
   - If not → the run crashed/failed. Auto-restart with the same
     ``--run-dir`` (resume mode; completed samples are skipped, failed
     samples are retried).
2. **Stall detection**: if no new sample file has appeared in
   ``--stall-minutes`` minutes, write a stall alert (the run is hung on
   a single sample — likely a network timeout on a very long
   conversation, not a crash).
3. **Failure-rate alert**: count ``WARN: sample ... failed`` lines in
   the run log. If the last ``--failure-window`` samples all failed
   (e.g., mimo-v2.5 quota exhausted, tunnel down for hours), write an
   alert so the user can intervene before the run "completes" with 0/100
   valid samples.

The watchdog writes a human-readable status line to ``--status-file``
every cycle so a single ``cat`` shows the current health.

CLI::

    setsid nohup uv run python -m benchmarks.longmemeval.run_health \\
        --run-pid 425199 \\
        --run-dir runs/publication/s8-stratified100 \\
        --status-file runs/publication/s8-stratified100.health \\
        --restart-command 'uv run python -m benchmarks.longmemeval.run \\
            --config configs/longmemeval/test50-mimo.toml \\
            --run-dir runs/publication/s8-stratified100 \\
            --sample-ids-file configs/longmemeval/stratified100.toml.inc' \\
        </dev/null >/dev/null 2>&1 &
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

DEFAULT_RUN_DIR = Path("runs/publication/s8-stratified100")


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is a live process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_finalized(run_dir: Path) -> bool:
    return (run_dir / "finalized" / "FINALIZED.json").exists()


def _count_samples(run_dir: Path) -> int:
    samples = run_dir / "samples"
    if not samples.exists():
        return 0
    return sum(
        1
        for p in samples.glob("*.json")
        if "extraction_snapshot" not in p.name
    )


def _newest_sample_mtime(run_dir: Path) -> float:
    """Return the mtime of the newest sample file (0 if none)."""
    samples = run_dir / "samples"
    if not samples.exists():
        return 0.0
    mtimes = [
        p.stat().st_mtime
        for p in samples.glob("*.json")
        if "extraction_snapshot" not in p.name
    ]
    return max(mtimes) if mtimes else 0.0


def _count_failed_samples_in_log(log_path: Path) -> int:
    """Count ``WARN: sample ... failed`` lines in the run log."""
    if not log_path.exists():
        return 0
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    # The run prints one summary line per failed-sample batch plus
    # per-sample WARN lines. Count the per-sample lines.
    return text.count("WARN: sample ") + text.count("samples failed: ")


def _write_status(
    status_path: Path,
    *,
    state: str,
    samples: int,
    cache: int,
    message: str,
) -> None:
    line = (
        f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] state={state} "
        f"samples={samples}/100 cache={cache} | {message}"
    )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(line + "\n", encoding="utf-8")
    print(line, flush=True)


def _write_alert(alert_path: Path, message: str) -> None:
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    alert_path.write_text(
        f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}\n",
        encoding="utf-8",
    )


def _restart_run(restart_command: str, log_path: Path) -> int | None:
    """Restart the run in the background. Returns the new PID or None."""
    try:
        # Append to the same log so all run output is in one place.
        log_handle = log_path.open("a", buffering=1)
        proc = subprocess.Popen(
            shlex.split(restart_command),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid
    except Exception as exc:
        _write_alert(
            log_path.parent / "s8-run-restart-failed",
            f"restart failed: {exc}",
        )
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "S8 run-health watchdog: auto-restart the benchmark process "
            "on crash + alert on stall/high-failure-rate."
        )
    )
    parser.add_argument(
        "--run-pid",
        type=int,
        required=True,
        help="PID of the benchmarks.longmemeval.run process to monitor.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_DIR,
        help="Run directory (for sample count + FINALIZED check).",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Run log path (for WARN parsing). Default: <run-dir>.log",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Human-readable health status file. Default: <run-dir>.health",
    )
    parser.add_argument(
        "--restart-command",
        type=str,
        default=None,
        help=(
            "Shell command to restart the run (resume mode). Must reuse "
            "the same --run-dir so completed samples are skipped."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=300.0,
        help="Check interval in seconds (default: 300 = 5 min).",
    )
    parser.add_argument(
        "--stall-minutes",
        type=float,
        default=45.0,
        help=(
            "Alert if no new sample appears within this many minutes "
            "(default: 45). A single sample takes ~12 min; 45 min covers "
            "the longest conversations + retry backoff."
        ),
    )
    parser.add_argument(
        "--max-failed-window",
        type=int,
        default=5,
        help=(
            "Alert if this many samples failed in total (default: 5). "
            "More than 5 failed samples across a 100-sample run suggests "
            "a systemic issue (quota, tunnel, gateway)."
        ),
    )
    parser.add_argument(
        "--alert-dir",
        type=Path,
        default=Path("/tmp"),
        help="Directory for alert flag files.",
    )
    args = parser.parse_args(argv)

    log_path = args.log or (args.run_dir.with_suffix(".log"))
    status_path = args.status_file or (args.run_dir.with_suffix(".health"))
    current_pid = args.run_pid
    last_sample_count = _count_samples(args.run_dir)
    last_progress_mtime = _newest_sample_mtime(args.run_dir)
    restart_count = 0
    max_restarts = 3

    _write_status(
        status_path,
        state="STARTED",
        samples=last_sample_count,
        cache=0,
        message=f"monitoring PID={current_pid}",
    )

    while True:
        time.sleep(args.interval)
        samples = _count_samples(args.run_dir)
        cache = sum(
            1
            for _ in (args.run_dir / "model_cache").rglob("*")
            if _.is_file()
        ) if (args.run_dir / "model_cache").exists() else 0
        newest_mtime = _newest_sample_mtime(args.run_dir)
        failed_in_log = _count_failed_samples_in_log(log_path)

        # 1. Completion check.
        if _is_finalized(args.run_dir):
            _write_status(
                status_path,
                state="COMPLETED",
                samples=samples,
                cache=cache,
                message=f"FINALIZED.json present; run done (failed_in_log={failed_in_log})",
            )
            return 0

        # 2. Process liveness.
        if not _pid_alive(current_pid):
            if restart_count >= max_restarts:
                _write_status(
                    status_path,
                    state="GIVE_UP",
                    samples=samples,
                    cache=cache,
                    message=(
                        f"PID {current_pid} dead; restarted "
                        f"{restart_count}x, giving up (manual check needed)"
                    ),
                )
                _write_alert(
                    args.alert_dir / "s8-run-dead",
                    f"PID {current_pid} dead after {restart_count} restarts; "
                    f"samples={samples}/100; manual intervention required",
                )
                # Keep running so the status file updates if the user
                # manually restarts the run.
                time.sleep(args.interval * 2)
                continue
            if args.restart_command:
                _write_status(
                    status_path,
                    state="RESTARTING",
                    samples=samples,
                    cache=cache,
                    message=f"PID {current_pid} dead; restart #{restart_count + 1}",
                )
                new_pid = _restart_run(args.restart_command, log_path)
                if new_pid is not None:
                    current_pid = new_pid
                    restart_count += 1
                    last_sample_count = samples
                    last_progress_mtime = _newest_sample_mtime(args.run_dir)
                    _write_status(
                        status_path,
                        state="RESTARTED",
                        samples=samples,
                        cache=cache,
                        message=f"new PID={new_pid} (restart #{restart_count})",
                    )
                else:
                    _write_alert(
                        args.alert_dir / "s8-run-restart-failed",
                        f"restart #{restart_count + 1} failed; PID was {current_pid}",
                    )
                continue
            else:
                _write_status(
                    status_path,
                    state="DEAD_NO_RESTART",
                    samples=samples,
                    cache=cache,
                    message=f"PID {current_pid} dead; no --restart-command given",
                )
                _write_alert(
                    args.alert_dir / "s8-run-dead",
                    f"PID {current_pid} dead; samples={samples}/100; "
                    f"rerun with --resume-dir {args.run_dir}",
                )
                continue

        # 3. Stall detection (process alive but no progress).
        if samples > last_sample_count:
            last_sample_count = samples
            last_progress_mtime = newest_mtime
        else:
            stall_seconds = time.time() - last_progress_mtime
            if stall_seconds > args.stall_minutes * 60:
                _write_status(
                    status_path,
                    state="STALLED",
                    samples=samples,
                    cache=cache,
                    message=(
                        f"no new sample in {stall_seconds / 60:.0f} min "
                        f"(PID {current_pid} alive but stuck; likely a "
                        f"long conversation or network timeout; check "
                        f"tunnel log)"
                    ),
                )
                _write_alert(
                    args.alert_dir / "s8-run-stalled",
                    f"no new sample in {stall_seconds / 60:.0f} min; "
                    f"samples={samples}/100; PID {current_pid} alive",
                )
                continue

        # 4. Failure-rate alert.
        if failed_in_log >= args.max_failed_window:
            _write_status(
                status_path,
                state="HIGH_FAILURE",
                samples=samples,
                cache=cache,
                message=(
                    f"{failed_in_log} failed samples in log (threshold "
                    f"{args.max_failed_window}); likely systemic issue "
                    f"(quota/tunnel/gateway); check {log_path}"
                ),
            )
            _write_alert(
                args.alert_dir / "s8-high-failure",
                f"{failed_in_log} samples failed; samples={samples}/100; "
                f"check {log_path} for WARN lines",
            )
            continue

        # 5. Healthy progress.
        delta = samples - last_sample_count
        _write_status(
            status_path,
            state="OK",
            samples=samples,
            cache=cache,
            message=(
                f"PID {current_pid} alive; +{delta} samples since "
                f"last check"
            ),
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nrun-health watchdog stopped", file=sys.stderr)
        raise SystemExit(0) from None
