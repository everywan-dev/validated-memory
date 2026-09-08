# Canonical views are inert and the app is opt-in

Accepted for 2.0. Canonical knowledge.html and memory.html remain deterministic,
self-contained and script-free; knowledge-app.html is a separately opted-in
enhancement whose sole inline script can be removed to recover the exact
canonical knowledge page. Presence activates regeneration rather than a new
configuration key that older validators would reject; an active app implies a
canonical knowledge page, including under unattended refresh, but not an absent
memory page. All pages share the fixed CSP, so the canonical whitelist must
independently prove that its permitted inline script is not actually present.
