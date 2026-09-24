#!/bin/sh
# One mount, /harness-profile, holds the profile and its files.
set -eu

ROOT=/harness-profile

if [ -z "${HARNESS_PROFILE:-}" ]; then
  if [ -f "$ROOT/profile.toml" ]; then
    HARNESS_PROFILE=$ROOT
  elif [ -f "$ROOT/profile/profile.toml" ]; then
    HARNESS_PROFILE=$ROOT/profile
  else
    echo "bizharness: no profile.toml in $ROOT or $ROOT/profile" >&2
    exit 1
  fi
  export HARNESS_PROFILE
fi

if [ -z "${HARNESS_DATA_ROOT:-}" ]; then
  if [ -d "$ROOT/harness-data" ]; then
    HARNESS_DATA_ROOT=$ROOT/harness-data
  elif [ -d "$ROOT/data" ]; then
    HARNESS_DATA_ROOT=$ROOT/data
  else
    HARNESS_DATA_ROOT=$ROOT/harness-data
    mkdir -p "$HARNESS_DATA_ROOT"
  fi
  export HARNESS_DATA_ROOT
fi

exec bizharness serve --host 0.0.0.0 --port 8811 "$@"
