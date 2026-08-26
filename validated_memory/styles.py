"""The stylesheet of each page, hand-written, one per page.

Split so that restyling one view cannot restyle the other. `html.page` takes
the stylesheet as an argument and each view passes its own; there is no
shared constant left to edit by accident. `MEMORY` is the stylesheet both
pages shared before the split, kept byte for byte -- the memory view's
markup, content and styling are out of scope for the 2.0.0 change and must
not move -- and `KNOWLEDGE` starts as the same bytes and is the one that
grows.

No third-party CSS. Nothing here is generated, downloaded or vendored, so
there is no build step, no lockfile, no asset manifest, no hash to verify and
no third-party licence landing in an adopter's repository. The cost is that
this file is written by hand; the benefit is that the page opens with no
network and renders the same bytes forever.

These strings are inlined verbatim inside `<style>`. CSS has no escaping that
survives being parsed as CSS, so nothing adopter-authored may ever reach
here: every value in this file is written in this repository.
"""

KNOWLEDGE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
"""

MEMORY = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
"""
