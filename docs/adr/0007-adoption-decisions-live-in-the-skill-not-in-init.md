# Adoption decisions live in the skill, not in `init`

Adopting the plugin asks one thing of the adopter that no command can
answer for them: whether the repository versions the validated-memory
layout, or keeps it local to the clone -- and, if versioned, whether the
derived files and the HTML views are versioned too. The question has a
deadline: the first `init --harness-memory`, which the `SessionStart` hook
runs by itself at the next session start of an adopted project, absorbs the
harness's existing agent memory into `memory/`, and in a repository that
versions the layout that memory is in the next commit.

The decision: the questions, and the writing of the ignore list for the
"local" answers, belong to the `adopt-validated-memory` skill. `init`
stays non-interactive and never touches git: it scaffolds the layout and
nothing else, the same whether a person, a hook, CI or a script calls it.
The ignore list is fixed prose in the skill and in the adoption guide, and
a test pins both against what `init`, `derive` and `probe` really create at
the adopter's root -- so a new root artifact cannot appear without both
texts learning to ignore it, and a retired one cannot linger.

## Considered options

- **Flags on `init`** (`--layout-policy`, `--derived-policy`,
  `--view-policy`, each `versioned|gitignore|exclude`) that write a managed
  block before scaffolding -- rejected, for now. It puts git knowledge
  inside a command whose contract is "create each item only if missing,
  never touch an existing one": editing `.gitignore`, resolving the exclude
  file of a linked worktree, checking that nothing listed is already
  tracked, are all mutations of files `init` does not own, with their own
  failure modes. The hook runs `init` unattended on every session start,
  and a flag it does not pass is a flag nobody reviews. If the skill turns
  out to apply the list wrongly in practice, this is the option to revisit,
  and the test that pins the list is what makes the move safe.
- **A key in `validated-memory.md`** recording the policy -- rejected. An
  unknown field in the adopter configuration is an ERROR that gates every
  subcommand (see the reasoning in [Startup
  hooks](../reference/hooks.md) on presence-based activation of the views);
  the ignore rules themselves are the record, readable by git without the
  plugin.
- **Not asking, and documenting "versioned" as the only shape** --
  rejected. The plugin's own repository adopts it with the layout kept
  local, and an adopter whose harness memory carries `user` and `feedback`
  facts must know, before the hook runs, that versioning publishes them.

## Consequences

- The skill asks before `init`, names both places a "local" answer can be
  written (`.gitignore`, or the exclude file resolved with `git rev-parse
  --git-path info/exclude`), and says what git cannot do: answer per remote
  for the same commit. Wanting the data on one host and not on another is
  a second history, which the plugin does not orchestrate.
- The ignore list exists three times on purpose -- skill, guide, and this
  repository's own `.gitignore` -- and the test keeps the first two equal
  to the CLI's behaviour. It covers the CLI's fixed root outputs, not the
  `--harness-memory` side effects (the symlink and the `.bak` live at
  PATH, outside the project in the hook's normal use) nor the
  `<view>.<pid>.tmp` files `render` leaves only after a hard kill.
- Every sentence that promised the memory "stays versioned" is now
  conditioned on the answer.
