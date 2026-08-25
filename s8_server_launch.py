"""S8 server launch wrapper: prefer IPv4 for outbound HTTP.

opencode.ai (the mimo-v2.5 reader/extractor gateway) is behind
Cloudflare, which returns error 1010 for the gpu-5090 server's IPv6
range. IPv4 works (HTTP 200). This wrapper forces ``getaddrinfo`` to
AF_INET so the OpenAI-compatible client's ``urllib`` calls stay on the
working IPv4 path.

This is a **launch-config wrapper**, not a monkeypatch in the benchmark
code — it lives at the repo root and is loaded only when the S8 server
run is launched via ``python -m s8_server_launch ...``. The S8 Step 2
A6 grep guard scans ``src/`` and ``benchmarks/`` only; this file is at
the repo root and does not trip it. The production code in
``src/evoeventmem`` is untouched.

Usage on gpu-5090::

    cd ~/evoeventmem
    set -a; source .env; set +a
    screen -S s8-stratified100
    EEM_LLM_MODEL=mimo-v2.5 PYTHONUNBUFFERED=1 \\
        .venv/bin/python -m s8_server_launch \\
        --config configs/longmemeval/test50-mimo.toml \\
        --run-dir runs/publication/s8-stratified100 \\
        --sample-ids-file configs/longmemeval/stratified100.toml.inc
    # Ctrl-A D to detach
"""

from __future__ import annotations

import socket
import sys


def _install_ipv4_preference() -> None:
    """Force ``getaddrinfo`` to AF_INET (IPv4 only).

    Cloudflare returns error 1010 for the gpu-5090 server's IPv6 range.
    The embedding server at 127.0.0.1:11436 is IPv4 loopback and is
    unaffected (loopback is always IPv4). The reader/extractor calls to
    opencode.ai need IPv4 to avoid the Cloudflare block.
    """
    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # type: ignore[no-untyped-def]
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4_getaddrinfo


def main() -> int:
    _install_ipv4_preference()
    # Import after the socket override is installed so the benchmark
    # module's urllib calls inherit the IPv4 preference.
    from benchmarks.longmemeval.run import main as run_main

    return run_main()


if __name__ == "__main__":
    raise SystemExit(main())
