# Add bounded local recall and project-memory consultation

The plugin maintains memory and knowledge validity but has no task retrieval
command. Startup reports status, the knowledge index lacks topic descriptions,
and adoption guidance checks claims before citation without directing agents
to consult prior work before substantial investigation.

Implement additive read-only recall over the two existing layers, with bounded
text/JSON results, literal source pointers, explicit evidence/freshness and
supersession, deterministic lexical ranking and on-demand navigation. Add a
consultation skill and an explicit adopter upgrade path. Preserve existing
schemas, indexes, exit 0/1/2 semantics and other commands' finding shapes.

New retrieval failures must be explicit: invalid, unsafe or unreadable requested
inputs produce exit 1 and no usable results; zero matches and bounded selection
over valid inputs produce exit 0. No automatic probes, persisted index, external
service or mandatory action gate. POSIX descriptor-relative acquisition is the
initial supported implementation; unsupported platforms fail explicitly.

Acceptance: CLI subprocess and structural tests, independent review and engineer
adversarial checks, a bounded synthetic usefulness comparison and final architect
acceptance. The closed historical pilot is not resumed. Implementation is
authorized; version bump, merge and publication are separate release work.

Specification: docs/plans/memory-reuse/specification.md.
Architecture: docs/adr/0016-retrieval-is-discovery-not-validation.md.
