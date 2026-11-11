#!/bin/zsh
set -euo pipefail

exec python3 "${0:A:h}/check_arm64.py" "$@"

