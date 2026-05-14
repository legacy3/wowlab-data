#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  pnpm install --frozen-lockfile
fi

needs_supabase=1
for arg in "$@"; do
  case "$arg" in
    --dry-run|--skip-assets|--help|-h)
      needs_supabase=0
      ;;
  esac
done

if [ "$needs_supabase" -eq 1 ]; then
  if [ -z "${SUPABASE_URL:-}" ]; then
    read -r -p "Supabase project URL: " SUPABASE_URL
    export SUPABASE_URL
  fi

  if [ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
    read -r -s -p "Supabase service role key: " SUPABASE_SERVICE_ROLE_KEY
    printf '\n'
    export SUPABASE_SERVICE_ROLE_KEY
  fi
fi

pnpm refresh -- --force "$@"
