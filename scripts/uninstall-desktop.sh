#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-}"
if [[ -z "$REPO_ROOT" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

exec "$REPO_ROOT/cli/saga" uninstall "${@:2}"
