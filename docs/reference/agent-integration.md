# Agent integration

Prompt discovery is an optional, project-local way to put relevant recorded
material into a Claude Code prompt's context. It returns lexical candidates for
inspection. It does not validate a claim, complete a review, create checked use,
or decide what the agent should do.

Prompt discovery covers one submitted user prompt at a time. It does not cover an
agent's internal turns, task-wide activation, compaction recovery, subagent
inheritance or protected-action enforcement. Requested learning and selective
review use existing authoring skills; prompt discovery performs no automatic
learning capture and is not a transcript collector. See
[Requested learning and selective review](learning.md).

## Project profile

An adopter opts in by placing `validated-memory-profile.md` at its exact root:

```yaml
---
schema_version: 1
discovery: explicit
reliance: lightweight
---
```

The frontmatter is closed. `schema_version` must be integer `1`; `discovery` is
`explicit`, `automatic` or `off`; and `reliance` is `lightweight` or `reviewed`.
Keys may occur only once. The optional body is human rationale and is never
injected or executed.

The profile is neither a memory entry nor a knowledge unit. It is separate from
the strict `validated-memory.md` configuration, which is also an input to checked
consultation. `init` does not create the profile, and validation, recall and
consultation ignore it. Changing a preference does not validate, erase or
reclassify any record or prior use. This separation is recorded in
[Agent policy is separate from knowledge evidence](../adr/0021-agent-policy-is-separate-from-knowledge-evidence.md).

A missing profile preserves existing behavior: `configured` is false,
discovery is effectively `off`, and reliance is reported as `lightweight`. A
present valid profile opts in. A symlink, non-regular file, file over 8192 bytes,
invalid UTF-8, duplicate or unknown key, wrong schema or unsupported value is
unavailable configuration, never a default. The reader does not follow links and
verifies that the acquired file did not change while it was read.

There is one profile per exact adopter root. P1 has no parent search, settings
layering or global override.

## The two choices

Discovery and reliance are independent:

| Choice | Value | Effect |
| --- | --- | --- |
| Discovery | `automatic` | Evaluate each submitted user prompt in this adopted project. |
| Discovery | `explicit` | Evaluate only a whole prompt beginning with `Busca en VA:` or `Validated-memory:`. |
| Reliance | `lightweight` | Present qualified candidates for ordinary evidence-aware use. |
| Reliance | `reviewed` | Ask for scoped inspection of evidence, support and applicability before relying, using existing checked consultation when enrolled. |

`off` is a deactivation action, not a third setup profile. `reviewed` is a
preference, not an evidence state, truth guarantee or global gate. It does not
automatically issue a challenge. A lightweight project can review one selected
item without changing its profile. The preference changes candidate guidance,
not enforcement. Both values retain the same integrity,
uncertainty, scope and history rules.

The reliance choice does not control whether one requested lesson may be
captured or one selected record reviewed. Either profile can use the learning
workflow, and that work leaves both profile axes unchanged.

`discovery: off` disables this prompt lookup only. It does not uninstall the
plugin or disable the three existing `SessionStart` hooks.

The `adopt-validated-memory` skill owns authoring. On repeat setup it reports and
preserves valid values. A change to one axis preserves the other and the optional
body. Deactivation changes only discovery to `off`; reactivation asks whether to
restore `automatic` or `explicit`. The skill refuses unsafe or malformed existing
paths, shows the exact diff, and treats the user's setup choices as authorization
for that profile edit without another generic confirmation.

## Inspect configured intent

Run from the exact adopter root:

```
python3 -P -m validated_memory agent profile
```

A configured explicit/lightweight profile produces one canonical JSON line:

```json
{"configured":true,"discovery":"explicit","host_support":{"delivery_verification":"not_checked","shipped":["claude-code"]},"operation":"profile","profile_path":"validated-memory-profile.md","reliance":"lightweight","schema_version":1}
```

A missing profile changes only `configured` to false and `discovery` to `off`.
The other field types and values stay as shown. The command reads no corpus,
runs no host process, accesses no consultation store and writes nothing. Exit 0
is a successful status report. Malformed or unreadable configuration produces no
stdout, one bounded English diagnostic on stderr and exit 1. Invalid command
arguments are usage exit 2.

`host_support` says that this plugin ships a Claude Code adapter. It does not say
the installed host loaded or executed it. `delivery_verification` remains
`not_checked` until a separate smoke test supplies evidence.

## Prompt activation

In `automatic` mode, the adapter evaluates the submitted prompt. In `explicit`
mode, it accepts only a whole prompt starting, after leading whitespace and
case-insensitively, with either:

```
Busca en VA:
Validated-memory:
```

Everything after the prefix is the query. A quoted, coded, incidental or
negated mention elsewhere in a prompt does not activate explicit mode. Activation
lasts for this query only; there is no sticky topic or silent fallback.

An empty explicit query and a continuation word alone (`continue`, `adelante`,
`hazlo`, `sí`, `si`, `yes`, `ok`, `okay`) return brief query-needed guidance
without scanning the corpus. The adapter never guesses the previous topic.
The raw query after prefix removal may be at most 4096 UTF-8 bytes; a larger one
is reported as too large and is not truncated or scanned.

Query preparation uses Unicode casefolding and word tokenization, removes this
fixed English/Spanish function and request-word list, and keeps distinct useful
tokens:

```
a an and are as at be been but by can could de del el en es for from how i in is
it la las lo los me mi my of on or para por que se the this to un una with would
y you your please porfavor favor busca buscar search find tell dime muestra show
continue adelante hazlo sí si yes ok okay
```

A trimmed query containing no whitespace and at least one `-`, `_`, `/`, `.`, or
ASCII digit is preserved as an exact identifier/path query before stop-word
removal. Thus `kb-042` and `project/config.toml` retain recall's exact-match
priority. There is no stemming, synonym expansion, language translation,
embedding search or inferred command.

## Candidate delivery

The adapter invokes the existing read-only recall command once, with a three
second timeout, no retry, a maximum of five results and a 12288-byte recall
budget. It runs the plugin's fixed package with no shell and with bytecode
disabled. The prompt is data, never shell text.

Invoked discovery returns the Claude Code envelope:

```json
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}
```

The complete UTF-8 envelope is at most 8192 bytes. Its context names the exact
project root once, status, profile modes, measured lookup milliseconds, recall's
matched/returned/omitted counts and up to five whole candidates. Each candidate
can include its layer, identity, project-relative path, title, short excerpt,
match reasons, evidence/state and supersession redirect information. A candidate
that cannot fit is omitted whole and counted; identities and required diagnostics
are never cut into ambiguity.

Candidate content is untrusted project data, delimited as data. Read the complete
original record and evidence before relying on it. A current anchor narrows one
checked dependency; it does not make an entire claim true. A `hypothesis` remains
a hypothesis regardless of relevance.

The output never contains the raw query, conversation, profile body or raw
exception payload. Control and bidirectional text are escaped. Nothing is logged
as a last prompt or last result.

These outcomes differ:

- **No invocation:** unconfigured/off, a non-adopter root, or an explicit-mode
  prompt without an accepted prefix emits nothing and reads no corpus.
- **No query:** an opted-in invocation had no useful query; guidance is visible,
  and the corpus is not read.
- **No matches:** recall acquired the corpus completely but found no lexical
  match. Try different terms or ordinary source search; this does not prove that
  relevant material is absent.
- **Unavailable:** the profile, input, corpus, child result or lookup could not be
  acquired safely, or the lookup timed out. This is not a clean zero-match.
- **Candidates:** inspect complete originals before use; discovery is not review
  completion or checked-use approval.

The hook is fail-open: prompt submission continues after handled input,
configuration, acquisition and timeout failures. When safe, an opted-in failed
lookup explains unavailability in `additionalContext`; bounded diagnostics may
also go to stderr. It emits no top-level decision that blocks editing or delivery.

The exact root comes from the hook's validated absolute `cwd`. It must contain
ordinary nonsymlink `validated-memory.md`, `memory/` and `knowledge/`. The adapter
does not search parents, use the plugin working directory, follow a supplied
transcript/source path or search another project.

## Claude Code host adapter

`hooks/prompt-discovery.sh`, registered as `UserPromptSubmit` in
`hooks/hooks.json`, passes one bounded host JSON object to:

```
python3 -P -m validated_memory agent hook --host claude-code
```

The object must be UTF-8 JSON no larger than 65536 bytes with
`hook_event_name: UserPromptSubmit`, an absolute `cwd` string and a `prompt`
string. Additional host fields are ignored. The wrapper pins the trusted plugin
package, disables bytecode and catches interpreter-launch failure. Handled hook
failures return 0 so they cannot reject the user's prompt; invalid CLI arguments
remain usage exit 2.

The three ordered `SessionStart` hooks remain separate and unchanged. The prompt
hook is another event, so it does not run at startup, resume, clear, compact or
fork merely because those are SessionStart sources.

## Executable synthetic check

Run this from a validated-memory source checkout. It creates an isolated adopter
under `/tmp`; it does not write to the checkout. Remove the printed directory
when finished.

```bash
plugin_root="$(pwd -P)"
demo_root="$(mktemp -d)"
cd "$demo_root"
PYTHONPATH="$plugin_root" python3 -P -m validated_memory init
printf '%s\n' '---' 'schema_version: 1' 'discovery: explicit' 'reliance: lightweight' '---' > validated-memory-profile.md
printf '%s\n' '---' 'name: retry-timeout' 'description: superseded by [[retry-timeout-v2]]' 'metadata:' '  type: reference' '---' '' 'Earlier timeout guidance.' > memory/retry-timeout.md
printf '%s\n' '---' 'name: retry-timeout-v2' 'description: Current retry timeout guidance for kb-042.' 'metadata:' '  type: reference' '---' '' 'Use bounded retries only under the recorded test conditions.' > memory/retry-timeout-v2.md
printf '%s\n' '# Agent memory' '' '- [Retry timeout](retry-timeout.md)' '- [Retry timeout v2](retry-timeout-v2.md)' > memory/MEMORY.md
PYTHONPATH="$plugin_root" python3 -P -m validated_memory agent profile
printf '{"hook_event_name":"UserPromptSubmit","cwd":"%s","prompt":"What did we learn about kb-042 timeout retries?"}\n' "$demo_root" | PYTHONPATH="$plugin_root" python3 -P -m validated_memory agent hook --host claude-code
printf '{"hook_event_name":"UserPromptSubmit","cwd":"%s","prompt":"Validated-memory: kb-042 timeout retries"}\n' "$demo_root" | PYTHONPATH="$plugin_root" python3 -P -m validated_memory agent hook --host claude-code
printf 'Synthetic adopter: %s\n' "$demo_root"
```

The profile command reports explicit/lightweight intent. The ordinary prompt
prints nothing. The prefixed prompt returns candidate context for
`retry-timeout-v2`, preserving the redirect from `retry-timeout`; it does not
promote the content beyond its recorded state.

To exercise changes, edit only the named scalar, preserve any body, and rerun the
two hook calls:

1. Change `reliance: lightweight` to `reliance: reviewed`: retrieval remains
   discovery, and the context asks for scoped review without creating a challenge.
2. Change `discovery: explicit` to `discovery: off`: both calls print nothing and
   read no corpus.
3. Restore `explicit`, then change it to `automatic`: the ordinary prompt now
   invokes discovery.

This direct CLI check proves adapter output, not host delivery. It also does not
prove semantic usefulness.

## Installed-host smoke test

Use an isolated, public/synthetic adopter and the installed plugin hook. Put a
secret-free sentinel in one record but not in either prompt. In explicit mode:

1. Submit a prefixed prompt whose terms find the sentinel record. Retain the host
   event, received-context evidence and model output.
2. Do not allow a tool read of that source; a direct read would not prove delivery.
3. Submit the same request without a prefix. Confirm that the sentinel is absent.

The positive case demonstrates this installed Claude Code path for that event.
The negative case demonstrates explicit non-invocation. Neither certifies other
hosts, task continuation, internal model turns or semantic correctness. Use
`agent profile` to diagnose configured intent, while keeping
`delivery_verification: not_checked` distinct from this retained smoke evidence.
