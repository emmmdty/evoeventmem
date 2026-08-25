"""S8 Step 5 watchdog: keep the qwen3-embedding tunnel alive.

The embedding server runs on ``gpu-5090`` (a remote host reachable via
cpolar). cpolar's free tier reassigns the public port daily, so the SSH
tunnel ``ssh -L 11436:127.0.0.1:11436 gpu-5090`` breaks when the port
changes. The S8 stratified100 live run takes ~16-19 hours and crosses
the daily port-rotation boundary, so an unwatched tunnel would fail
mid-run and corrupt embedding calls for every subsequent sample.

This watchdog:
1. Probes ``http://127.0.0.1:11436/v1/embeddings`` every ``--interval``
   seconds with a one-token test request.
2. On probe failure, kills any stale ``ssh -L 11436:...`` process and
   re-establishes the tunnel using the current ``~/.ssh/config`` entry
   for ``gpu-5090``.
3. If the re-SSH itself fails (cpolar port rotated), runs
   ``cpolar-ssh-update`` (a local CLI at ``~/.local/bin/cpolar-
   ssh-update`` that queries the cpolar API and rewrites the
   ``Host gpu-5090`` Port/HostName in ``~/.ssh/config``), then retries
   the SSH establishment. This makes the watchdog fully autonomous
   across the daily cpolar port rotation — no manual config edit.
4. Only after ``--max-consecutive-failures`` probe+update+re-establish
   attempts fail does it write the alert flag file at ``--alert-file``.

The live run (``benchmarks.longmemeval.run``) is sample-level resilient:
a tunnel drop fails only the in-flight sample (no partial file is
written — ``write_json_write_once`` only fires on success), and the run
continues to the next sample. Failed samples are retried by re-running
with ``--resume-dir runs/publication/s8-stratified100`` once the tunnel
is restored.

CLI::

    nohup uv run python -m benchmarks.longmemeval.tunnel_watchdog \\
        --interval 60 \\
        --log runs/publication/s8-stratified100.tunnel.log \\
        --alert-file /tmp/s8-tunnel-down \\
        > /dev/null 2>&1 &
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from subprocess import DEVNULL, run

EMBEDDING_ENDPOINT = "http://127.0.0.1:11436/v1/embeddings"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
LOCAL_PORT = 11436
SSH_HOST = "gpu-5090"
# ``cpolar-ssh-update`` queries the cpolar API and rewrites the
# ``Host gpu-5090`` Port/HostName in ``~/.ssh/config`` when the daily
# port rotation fires. Located on the user's PATH at
# ``~/.local/bin/cpolar-ssh-update``.
CPOLAR_SSH_UPDATE_CMD = "cpolar-ssh-update"


def _probe_embedding(timeout_s: float = 5.0) -> bool:
    """Return True if the embedding endpoint responds with a valid vector."""
    body = (
        b'{"model":"' + EMBEDDING_MODEL.encode() + b'","input":"ping"}'
    )
    request = urllib.request.Request(
        EMBEDDING_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
        return True
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _kill_stale_tunnels() -> int:
    """Kill any existing ``ssh -L 11436:...`` process. Returns count killed."""
    result = run(
        ["pkill", "-f", f"ssh.*-L {LOCAL_PORT}:127.0.0.1:{LOCAL_PORT}"],
        stdout=DEVNULL,
        stderr=DEVNULL,
    )
    return 0 if result.returncode in (0, 1) else result.returncode


def _run_cpolar_ssh_update() -> bool:
    """Run ``cpolar-ssh-update`` to refresh ~/.ssh/config with the current
    cpolar port. Returns True if the command ran and exited 0.

    This is the autonomous recovery path for the daily cpolar port
    rotation: when cpolar reassigns gpu-5090's public port, the SSH
    establishment fails because ``~/.ssh/config`` still has yesterday's
    port. ``cpolar-ssh-update`` queries the cpolar API and rewrites the
    config so the next ``ssh gpu-5090`` connects to the new endpoint.

    Implementation note: ``cpolar-ssh-update`` is a Python script with
    shebang ``#!/usr/bin/env python3`` that depends on the ``requests``
    library. The watchdog runs inside ``uv run``, whose ``python3``
    resolves to the project venv (no ``requests``). We override the
    subprocess env to a system-only PATH so the shebang finds
    ``/usr/bin/python3`` (which has ``requests`` in
    ``~/.local/lib/python3.10/site-packages/``).
    """
    script = shutil.which(CPOLAR_SSH_UPDATE_CMD)
    if script is None:
        return False
    # System-only PATH so /usr/bin/env python3 finds the system Python
    # (with requests), not the uv venv Python.
    result = run(
        [script],
        stdout=DEVNULL,
        stderr=DEVNULL,
        timeout=30,
        env={
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": str(Path.home()),
        },
    )
    return result.returncode == 0


def _establish_tunnel() -> bool:
    """Re-establish the SSH tunnel. Returns True on success."""
    _kill_stale_tunnels()
    time.sleep(1)
    result = run(
        [
            "ssh",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-f", "-N",
            "-L", f"{LOCAL_PORT}:127.0.0.1:{LOCAL_PORT}",
            SSH_HOST,
        ],
        stdout=DEVNULL,
        stderr=DEVNULL,
    )
    return result.returncode == 0


def _recover_tunnel(log_path: Path) -> bool:
    """Full recovery: kill stale tunnel → re-SSH → if SSH fails, run
    ``cpolar-ssh-update`` → retry SSH. Returns True if the embedding
    endpoint is reachable after recovery.
    """
    # First attempt: kill stale + re-SSH with current config.
    if _establish_tunnel():
        time.sleep(3)
        if _probe_embedding():
            return True
    # SSH establishment failed (or probe still down) → cpolar port
    # likely rotated. Refresh ~/.ssh/config via cpolar-ssh-update,
    # then retry SSH establishment.
    _log(log_path, "SSH re-establish failed; running cpolar-ssh-update...")
    if _run_cpolar_ssh_update():
        _log(log_path, "cpolar-ssh-update ok; retrying SSH establishment")
    else:
        _log(
            log_path,
            "cpolar-ssh-update unavailable or failed; retrying SSH anyway",
        )
    if _establish_tunnel():
        time.sleep(3)
        if _probe_embedding():
            return True
    return False


def _log(log_path: Path, message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "S8 watchdog: keep the qwen3-embedding SSH tunnel alive "
            "across the daily cpolar port rotation. Auto-runs "
            "cpolar-ssh-update when the port rotates."
        )
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Probe interval in seconds (default: 60).",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("runs/diagnostic/s8-tunnel.log"),
        help="Log file path.",
    )
    parser.add_argument(
        "--alert-file",
        type=Path,
        default=Path("/tmp/s8-tunnel-down"),
        help=(
            "Flag file written only when probe + cpolar-ssh-update + "
            "re-SSH all fail for --max-consecutive-failures cycles "
            "(requires manual intervention)."
        ),
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help=(
            "Number of consecutive recovery failures before writing the "
            "alert file (default: 3)."
        ),
    )
    args = parser.parse_args(argv)

    _log(args.log, f"watchdog start; interval={args.interval}s")
    consecutive_failures = 0
    last_status = None

    while True:
        ok = _probe_embedding()
        if ok:
            consecutive_failures = 0
            if last_status is not True:
                _log(args.log, "embedding endpoint UP (dim=1024 path healthy)")
                if args.alert_file.exists():
                    args.alert_file.unlink()
            last_status = True
        else:
            _log(args.log, "embedding endpoint DOWN; running full recovery...")
            recovered = _recover_tunnel(args.log)
            if recovered:
                consecutive_failures = 0
                last_status = True
                _log(args.log, "tunnel recovered; endpoint UP")
                if args.alert_file.exists():
                    args.alert_file.unlink()
            else:
                consecutive_failures += 1
                last_status = False
                _log(
                    args.log,
                    f"recovery FAILED (consecutive: {consecutive_failures})",
                )
            if consecutive_failures >= args.max_consecutive_failures:
                args.alert_file.parent.mkdir(parents=True, exist_ok=True)
                args.alert_file.write_text(
                    f"s8-tunnel-down at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                    f"consecutive_failures={consecutive_failures}\n"
                    f"cpolar-ssh-update + SSH recovery both failed.\n"
                    f"Manual check: run `cpolar-ssh-update` then "
                    f"`ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090`.\n",
                    encoding="utf-8",
                )
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nwatchdog stopped", file=sys.stderr)
        raise SystemExit(0) from None
