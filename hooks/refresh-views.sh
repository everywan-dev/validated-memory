#!/bin/bash
# refresh-views.sh -- SessionStart hook for the validated-memory plugin.
#
# Keeps whichever static HTML views an adopter has already activated
# (`init --view`) fresh, so nobody has to remember to re-run `render` by
# hand after the corpus changes. This is a separate script from
# restore-memory-symlink.sh on purpose: that hook's contract turns on
# "never loses data" -- absorb, park, link, delete nothing -- while this one
# runs a generator that overwrites HTML files. Two contracts in one script
# would make it impossible to review either.
#
# `render --only-existing` regenerates only the artifacts already on disk
# and creates none (see validated_memory/render.py). An adopter who never
# ran `init --view` has no artifacts, so this hook finds nothing to do and
# costs them nothing.
#
# Fail-open, unconditionally: this script never deletes a file, and it
# always exits 0 -- a SessionStart hook must never be able to break session
# startup, whatever it finds.
#
# "Adopted" is the same test restore-memory-symlink.sh uses: the project
# directory the harness just opened (`$CLAUDE_PROJECT_DIR`) has both
# `validated-memory.md` and `memory/` at its root. Anything else -- not
# adopted yet, only half-scaffolded, or no project directory at all -- is a
# clean no-op.

set -u

if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  exit 0
fi

# Normalize away a trailing slash so paths built below never double a '/'.
project_dir="${CLAUDE_PROJECT_DIR%/}"

if [ ! -f "$project_dir/validated-memory.md" ] || [ ! -d "$project_dir/memory" ]; then
  # Not an adopter project (or only half-scaffolded): nothing to refresh.
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "refresh-views: python3 not found on PATH; skipping" >&2
  exit 0
fi

# The plugin's own package root: this script lives at <plugin root>/hooks/,
# so its parent directory is where `validated_memory/` lives. Computed from
# the script's own path rather than trusted to `$CLAUDE_PLUGIN_ROOT` alone,
# so it also works when this repo is exercised directly (tests, or a manual
# run) without going through a full plugin install.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [ -z "$script_dir" ]; then
  echo "refresh-views: could not resolve the plugin's own path; skipping" >&2
  exit 0
fi
plugin_root="$(dirname "$script_dir")"

(
  cd "$project_dir" 2>/dev/null || exit 0
  PYTHONPATH="$plugin_root${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m validated_memory render --only-existing >/dev/null
)

# Whatever happened inside the subshell above -- nothing to render, one or
# both views regenerated, or even an internal error -- this hook always
# reports success: render's own fail-open WARNINGs are already visible on
# stderr (stdout alone is silenced), and a SessionStart hook must never
# gate session startup.
exit 0
