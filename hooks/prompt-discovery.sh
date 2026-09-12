#!/bin/bash
# Fail-open Claude Code UserPromptSubmit adapter.
# The Python adapter owns all parsing and bounded discovery.  This wrapper
# supplies only the trusted package location and disables bytecode writes.

set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [ -z "$script_dir" ]; then
  echo "prompt-discovery: could not resolve the plugin path; skipping" >&2
  exit 0
fi
plugin_root="$(dirname "$script_dir")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "prompt-discovery: python3 not found on PATH; skipping" >&2
  exit 0
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$plugin_root" \
  python3 -P -m validated_memory agent hook --host claude-code
status=$?
if [ "$status" -ne 0 ]; then
  echo "prompt-discovery: adapter unavailable; continuing" >&2
fi
exit 0
