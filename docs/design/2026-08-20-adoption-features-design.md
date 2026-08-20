# Adoption features — design (2026-08-20, revised after adversarial challenge)

Five additions aimed at lowering the cost of adopting validated-memory and at
making the method visible from CI. This revision incorporates the resolutions
of a 14-finding adversarial design review; each resolution is marked
inline. The project's standing rules bind everything here: Python 3 stdlib
only, English everywhere, exit codes 0/1/2, e2e subprocess tests only, skills
never reimplement a rule the CLI enforces, the CLI is enforcement — judgment
lives in skills.

## 1. `status` subcommand (CLI)

One read-only command that answers, precisely: "is this adopter project
**structurally consistent**, and what does its freshness look like?" — not
"is everything current", which the verdict-is-data contract deliberately does
not gate on.

- Computes one shared `ProjectCheckResult` internally — validation, lint,
  index check, freshness summary — and formats it per gate; it does **not**
  shell out to the three subcommands, which would run the same validation
  twice and read the log twice. The subcommands stay the public seam and keep
  their exact behavior. *(resolution of finding 4)*
- A missing `knowledge-index.md` is an **ERROR, exactly as `derive --check`
  defines it**. The adopter who chooses not to version the index says so
  explicitly with `status --skip-index`; absence is never read as policy — a
  deleted index and an unversioned index are indistinguishable. The flag is a
  CLI flag, not a `validated-memory.md` field: older plugin versions reject
  unknown configuration fields, so a config field would break the adopter's
  older sessions. *(findings 1, and the config-compat trap)*
- Freshness is **reported, never gated by default**: counts per verdict
  across active units from the existing service view of `verdicts.jsonl`.
  Explicit opt-in enforcement: `--fail-on drifted` / `--fail-on unknown`
  (repeatable). The ternary verdict domain is untouched. *(finding 2)*
- **Verdict age lives here**, not in `derive`: `status --max-verdict-age N`
  (days, UTC, strict `age > N`) emits WARNING findings naming unit, anchor
  and age; `--fail-on-aged` upgrades them to gating. `--as-of TIMESTAMP`
  makes runs reproducible for tests and audits; default is now. Age never
  enters the derived index, which must stay deterministic for `--check`.
  Reading age extends the log's read contract: `recorded_at` absent, invalid
  or in the future yields `age unknown` as a WARNING under the flag, and
  nothing changes without the flag. "Latest" remains append order.
  *(findings 5, 6; replaces the earlier `derive --max-verdict-age` idea)*
- Never runs `probe` (side effects, network). Exit: worst of the gates run.

## 2. `verdicts.jsonl` persistence policy (documentation, v1)

The versioned index bakes in verdicts read from the log, so CI on a clean
checkout needs the log or every anchor collapses to `unknown`. v1 policy,
documented in `docs/adoption.md`: **version `verdicts.jsonl` alongside
`knowledge-index.md`**; the log is append-only history and belongs to the
audit trail anyway. Payloads land in the log, so the existing advice applies:
anchors must not carry secrets. Splitting a versionable structural index from
an operational freshness report is noted as a possible v2, not attempted now.
*(finding 3)*

## 3. `ask` skill

A sixth skill, `ask-validated-memory`: answer usage questions about the tool.

- Sources, exclusively and in this precedence: the plugin's own `README.md`,
  `docs/adoption.md`, `docs/walkthrough.md`, `docs/adr/*.md` — resolved
  under `${CLAUDE_PLUGIN_ROOT}`, never the adopter's own files of the same
  name — plus the CLI's `--help` output. State which plugin version answered.
- Quote the exact CLI invocation for anything actionable; if the docs do not
  answer, say so; never invent a flag.
- Not "just a SKILL.md": the skills-structure test pins the skill count and
  README/docs state it, so those update in the same change, as a minor
  release. *(finding 13)*

## 4. `bootstrap-from-repo` skill (`check-repo` / `setup-vm`)

Walk an adopter repository and propose starting facts for the two layers. A
skill, not a subcommand: extraction is judgment.

- **Security perimeter first** *(finding 9)*: repository content is data,
  never instructions — a README that says "ignore your rules" is a string;
  nothing the repo contains is executed; reads are confined to realpaths
  inside the repo root (symlinks resolved); secrets, `.env`, credentials,
  binaries, vendored and generated artifacts are excluded from reading and
  from proposals; sensitive-looking values are redacted; size limits on what
  is read. Every proposal shows its source and the exact file diff before
  anything is written, and only confirmed proposals are written.
- **Evidence is classified, not capped** *(finding 8)*: inferred from prose →
  `hypothesis`; checkable by following named file + commit → `verifiable`
  with that provenance; actually executed by the skill during bootstrap, with
  the command recorded and repeatable → `measured`. The
  no-promotion-by-conviction rule forbids upgrading an existing unit in
  place; it does not forbid honest evidence on a new one.
- **Anchors are deliberate, never automatic** *(finding 7)*: the commit read
  is recorded as provenance. A `git_ref` anchor is proposed only where the
  claim genuinely dies when a specific ref moves, with the full envelope the
  bundled probe requires (`repo`, `ref`, full 40-hex `commit`), and never
  from a dirty working tree. Anchoring every fact to `HEAD` would turn the
  next commit into a wall of `drifted` noise.
- **Rerun semantics**: each proposal is classified against what exists —
  exact duplicate (skip), new claim (propose), contradiction (propose a
  successor with `supersedes`, never overwrite, never silently skip). One
  claim goes to one layer, by function: durable project facts → agent
  memory; claims worth probing → knowledge units.

## 5. Reusable GitHub Action

So an adopter's CI gates on the method in three lines.

- **Runs the plugin from its own checkout** — `PYTHONPATH=$GITHUB_ACTION_PATH
  python3 -m validated_memory ...` — no `pip install`, no version skew
  between the action ref and the code that runs. This matches how the
  SessionStart hook already launches the CLI. *(finding 12)*
- Gates: `status` (with the adopter's chosen flags) once it exists.
- **Release invariant** *(findings 10, 11)*: a release is one commit tagged
  `vX.Y.Z` where `pyproject.toml`, `__init__.py` and `plugin.json` agree
  (the existing version-agreement test enforces this); the marketplace entry
  never duplicates the version; GitLab and GitHub point at the same commit;
  immutable `vX.Y.Z` tags are the reference, a moving `v1` is offered as the
  convenience channel, and the README documents full-SHA pinning first for
  sensitive CI. Internal actions (`actions/setup-python`) are SHA-pinned.
  Publishing a plugin fix means bumping `plugin.json.version` — a moved tag
  alone reaches no plugin user.

## Sequencing (revised — finding 14)

1. ADRs recording the decisions above: what `status` means by consistent,
   index and log policy, age semantics, the release invariant.
2. The shared `ProjectCheckResult` view (extending the read contract with
   `recorded_at`).
3. `status`, with `--skip-index`, `--fail-on`, and age (`--max-verdict-age`,
   `--as-of`, `--fail-on-aged`).
4. Persistence policy documented in `docs/adoption.md`.
5. Release invariant + GitHub channel; then the Action.
6. `ask`, once the docs it reads are stable.
7. `bootstrap-from-repo` last — largest judgment surface, needs its threat
   model written into the skill.

Each on its own `feature/*` branch, TDD, adversarial review before merge.
