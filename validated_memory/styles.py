"""Independent, repository-owned stylesheets for the two offline pages.

Inlined verbatim: no adopter-authored CSS, external assets or build step.
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
h2 { font-size: 1rem; text-transform: uppercase; letter-spacing: .05em;
     margin: 1.25rem 0 .5rem; }
.overview { border: 1px solid rgba(127,127,127,0.35); border-radius: .5rem;
            padding: .25rem 1rem 1rem; margin-bottom: 2rem; }
table.counts { border-collapse: collapse; }
table.counts th, table.counts td { border: 1px solid rgba(127,127,127,0.35);
                                   padding: .25rem .6rem; text-align: right; }
table.counts th[scope="row"], table.counts thead th:first-child {
    text-align: left; }
table.counts tr.total th, table.counts td.total { font-weight: 600; }
ul.unprobed { list-style: none; padding-left: 0; }
ul.unprobed li { margin-bottom: .5rem; }
ul.groups { list-style: none; padding-left: 0; }
li.group { margin-bottom: .75rem; }
.group-name { font-weight: 600; }
ul.group-units { list-style: none; padding-left: 1rem; margin-top: .25rem;
                 border-left: 2px solid rgba(127,127,127,0.35); }
.unit { border: 1px solid rgba(127,127,127,0.35); border-radius: .5rem;
        padding: .25rem 1rem; margin-bottom: 1rem; }
.unit.superseded { border-style: dashed; }
.headline { font-weight: 600; }
.evidence, .verdict { font-size: .8em; border: 1px solid currentColor;
                      border-radius: 1rem; padding: .05rem .5rem; }
[data-evidence="measured"] > details > summary .evidence { border-style: solid; }
[data-evidence="verifiable"] > details > summary .evidence { border-style: dashed; }
[data-evidence="hypothesis"] > details > summary .evidence { border-style: dotted; }
[data-verdict="current"] > details > summary .verdict {
    background: rgba(46,125,50,0.18); }
[data-verdict="drifted"] > details > summary .verdict {
    background: rgba(198,40,40,0.18); }
[data-verdict="unknown"] > details > summary .verdict {
    background: rgba(117,117,117,0.18); }
ul.options, ul.anchors, ul.provenance { list-style: none; padding-left: 0; }
div.rationale { border-left: 3px solid rgba(127,127,127,0.35);
                padding-left: 1rem; margin: .75rem 0; }
.question { font-weight: 600; }
li.option { border: 1px solid rgba(127,127,127,0.35); border-radius: .35rem;
            padding: .4rem .6rem; margin-bottom: .4rem; }
li.option.chosen { border-width: 2px; }
.option-number { font-variant-numeric: tabular-nums; opacity: .7; }
.disposition { font-size: .8em; text-transform: uppercase;
               letter-spacing: .05em; }
.reason { margin: .25rem 0 0; }
svg { display: block; margin: .5rem 0; max-width: 100%; }
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
