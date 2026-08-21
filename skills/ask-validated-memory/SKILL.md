---
name: ask-validated-memory
description: Answer usage questions about validated-memory -- commands, flags, adoption steps, the method's rules -- from the plugin's own documentation, quoting exact invocations. Use when someone asks how validated-memory works or how to do something with it.
---

# Ask validated-memory

Answer questions about the tool from its own documentation -- never from
memory of similar tools, and never from the adopter project's files.

## Sources, in this order of precedence

Resolve every source under `${CLAUDE_PLUGIN_ROOT}`, never against files of
the same name in the current project:

1. `${CLAUDE_PLUGIN_ROOT}/README.md`
2. `${CLAUDE_PLUGIN_ROOT}/docs/adoption.md`
3. `${CLAUDE_PLUGIN_ROOT}/docs/walkthrough.md`
4. `${CLAUDE_PLUGIN_ROOT}/docs/adr/*.md` -- the reasons behind the rules
5. The CLI's own help:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -m validated_memory --help
```

The reference under `${CLAUDE_PLUGIN_ROOT}/docs/reference/` backs all of
them with per-command detail.

## Rules

- Quote the exact CLI invocation for anything actionable, copy-pastable as
  documented. Never invent a flag, a field, or a behavior: if the sources
  do not answer, say exactly that, and name the closest documented thing.
- State which plugin version answered, from:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -m validated_memory --version
```

- When the question is "why", answer from the ADR that records the
  decision and name it.
- When the question is about the adopter's own data (their units, their
  index, their verdicts), this skill is the wrong tool: point at
  `maintain-agent-memory`, `probe-freshness` or the CLI's enforcement
  commands instead of interpreting their files.
