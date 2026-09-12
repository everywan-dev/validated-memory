# Agent policy is separate from knowledge evidence

Agent discovery and reliance preferences live in the optional, independently
versioned `validated-memory-profile.md`, outside the closed adopter configuration
and both knowledge layers. Putting them in `validated-memory.md` would break older
validators and invalidate checked consultation inputs when only interaction policy
changed. Absence preserves existing behavior; adoption opts in explicitly and
existing commands do not acquire a new dependency on this profile.

Automatic and explicit discovery share the same read-only retrieval path. The
profile controls activation and workflow guidance, not evidence state, semantic
truth or a universal action gate. Lightweight learning does not disable selective
review; changing preferences does not retroactively validate or erase history.
The first host adapter delivers bounded query-level candidates through Claude
Code's prompt event; task lifetime and other host paths require separate evidence.

The adoption skill authors the profile with the user's selected choices, as it
does other adopter-owned instructions. Runtime profile inspection and discovery
remain read-only; no new canonical writer or journal bypass is introduced.
