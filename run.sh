#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  pnpm install --frozen-lockfile
fi

pnpm refresh -- --force "$@"
