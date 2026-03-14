#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "error: /etc/os-release not found" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

case "${ID:-}" in
  arch|endeavouros)
    echo "arch pacman"
    ;;
  fedora)
    echo "fedora dnf"
    ;;
  debian)
    echo "debian apt"
    ;;
  ubuntu)
    echo "ubuntu apt"
    ;;
  *)
    echo "error: unsupported distro '${ID:-unknown}'" >&2
    exit 1
    ;;
esac
