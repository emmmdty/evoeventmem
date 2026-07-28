#!/usr/bin/env bash
set -euo pipefail

name="${1:-}"
mkdir -p external
case "$name" in
  evomembench)
    git clone https://github.com/DSAIL-Memory/EvoMemBench.git external/EvoMemBench
    ;;
  longmemeval-v2)
    git clone https://github.com/xiaowu0162/LongMemEval-V2.git external/LongMemEval-V2
    ;;
  *)
    echo "usage: $0 {evomembench|longmemeval-v2}" >&2
    exit 2
    ;;
esac
