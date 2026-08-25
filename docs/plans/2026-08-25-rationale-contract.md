# `rationale` Contract Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the optional `rationale` field to the base contract, validated
structurally and lexically, with the two canonical documentation blocks kept in
sync by a test.

**Architecture:** One new field in `BASE_FIELDS`, one structural check
dispatched beside the existing per-field checks, and one lexical check that
runs where the raw document text is still in hand. Nothing renders it yet: this
plan ends with a corpus that can carry a rationale and a CLI that enforces its
shape.

**Tech Stack:** Python 3.11+, standard library only. pytest is the only
development dependency. The CLI is exercised as a subprocess; tests never
import the package's internals.

**Spec:** [`docs/design/2026-08-25-knowledge-view-decision-rationale.md`](../design/2026-08-25-knowledge-view-decision-rationale.md)

**Issue:** https://github.com/everywan-dev/validated-memory/issues/2

## Global Constraints

- Runtime code is Python 3, standard library only. pytest is the only
  development dependency.
- All content in this repository -- code, comments, CLI messages, docs, skills
  -- is written in English.
- Exit codes: `0` clean or WARNING-only, `1` ERROR, `2` usage error.
- Tests invoke the CLI as a subprocess over fixture adopter trees and never
  import the package's internals.
- Commits are Conventional Commits, in English.
- Every task ends with the full suite green: `python3 -m pytest`.
- This plan does **not** bump the version. The three version files move
  together in the release plan, per ADR 0005.

## Plan set

This is plan 1 of 3. Each produces working, testable software on its own.

1. **This plan** -- the `rationale` field: accepted, validated, documented.
2. **The page** -- the normalized model, the stylesheet split, the
   element-and-attribute whitelist, the overview and cards, the diagrams.
3. **The app and the release** -- `knowledge-app.html`, `init --view --app`,
   the adoption twin lists, the ADRs, the `v2` channel, 2.0.0.

---

### Task 1: The contract accepts `rationale`

**Files:**
- Modify: `validated_memory/contract.py:15`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BASE_FIELDS` now has six entries; the unknown-field message built
  at `validated_memory/contract.py:76` lists them all automatically.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_validate.py`, after `SUPERSEDING_UNIT` (line 21-30):

```python
RATIONALE_UNIT = """\
id: kb-0003
evidence: verifiable
rationale:
  question: "How should knowledge views be delivered?"
  options:
    - label: "Generate a complete static artifact"
      disposition: chosen
      reason: "It stays readable without Python, JavaScript or network access."
    - label: "Build an interactive application"
      disposition: rejected
      reason: "It makes the reader depend on a runtime."
anchors:
  - system: adopter-repo
    kind: git_ref
    captured_at: 2026-08-11T10:00:00Z
    payload: {}
"""


def test_a_unit_carrying_a_rationale_passes_clean(adopter_dir, write_unit, run_cli):
    write_unit("kb-0003.md", RATIONALE_UNIT)

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "rationale" not in result.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_validate.py::test_a_unit_carrying_a_rationale_passes_clean -v`
Expected: FAIL. stderr carries
`ERROR: knowledge/kb-0003.md: rationale: unknown field; the base contract declares id, evidence, supersedes, anchors, provenance`
and the exit code is 1.

- [ ] **Step 3: Write minimal implementation**

`validated_memory/contract.py:15`:

```python
BASE_FIELDS = ("id", "evidence", "supersedes", "anchors", "provenance", "rationale")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: PASS, and every other test in the file still passes. The
unknown-field message is built from `BASE_FIELDS` at
`validated_memory/contract.py:76`, so no message string needs editing.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all tests pass. Nothing else reads the field yet.

- [ ] **Step 6: Commit**

```bash
git add validated_memory/contract.py tests/test_validate.py
git commit -m "feat: the base contract accepts an optional rationale field"
```

---

### Task 2: Structural validation of `rationale`

**Files:**
- Modify: `validated_memory/contract.py` (constants beside `BASE_FIELDS`; a new
  `_check_rationale` and `_check_rationale_text`; one dispatch line in
  `_check_unit` at `validated_memory/contract.py:81-86`)
- Test: `tests/test_validate.py` (`INVALID_FIELDS`, lines 66-146)

**Interfaces:**
- Consumes: `BASE_FIELDS` from Task 1.
- Produces: `_check_rationale(location, data) -> list[Finding]`, dispatched
  from `_check_unit`. Finding fields are `rationale`,
  `rationale.question`, `rationale.options`, and
  `rationale.options[i].{label,disposition,reason}` -- the same
  `field[index].subfield` idiom the anchor checks already use
  (`anchors[0].captured_at`).

- [ ] **Step 1: Write the failing tests**

`INVALID_FIELDS` (`tests/test_validate.py:66`) is a list of
`(name, frontmatter, expected_field)` tuples that one test walks generically
(`tests/test_validate.py:149-160`); the consumer needs no change. Add these
tuples before the closing `]` at line 146:

```python
    (
        "rationale_not_a_mapping",
        'id: kb-0001\nevidence: measured\nrationale: "yes"\n',
        "rationale",
    ),
    (
        "rationale_unknown_key",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  consequences: "none"\n  options:\n    - label: "A"\n'
        '      disposition: chosen\n      reason: "R"\n    - label: "B"\n'
        '      disposition: rejected\n      reason: "R"\n',
        "rationale",
    ),
    (
        "rationale_question_missing",
        'id: kb-0001\nevidence: measured\nrationale:\n  options:\n'
        '    - label: "A"\n      disposition: chosen\n      reason: "R"\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "rationale.question",
    ),
    (
        "rationale_options_missing",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n',
        "rationale.options",
    ),
    (
        "rationale_options_too_few",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n',
        "rationale.options",
    ),
    (
        "rationale_no_chosen_option",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: rejected\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
        "rationale.options",
    ),
    (
        "rationale_two_chosen_options",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: chosen\n'
        '      reason: "R"\n',
        "rationale.options",
    ),
    (
        "rationale_option_not_a_mapping",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - "A"\n    - label: "B"\n      disposition: chosen\n'
        '      reason: "R"\n',
        "rationale.options[0]",
    ),
    (
        "rationale_option_unknown_key",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n      weight: "3"\n    - label: "B"\n'
        '      disposition: rejected\n      reason: "R"\n',
        "rationale.options[0]",
    ),
    (
        "rationale_option_reason_missing",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "rationale.options[0].reason",
    ),
    (
        "rationale_option_disposition_out_of_domain",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: maybe\n'
        '      reason: "R"\n    - label: "B"\n      disposition: chosen\n'
        '      reason: "R"\n',
        "rationale.options[0].disposition",
    ),
    (
        "rationale_labels_collide_after_whitespace",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "A "\n      disposition: rejected\n'
        '      reason: "R"\n',
        "rationale.options[1].label",
    ),
    (
        "rationale_label_carries_a_bidi_override",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A‮B"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
        "rationale.options[0].label",
    ),
```

Then add one test that pins what must **not** be rejected, because the point of
naming nine characters was to keep legitimate right-to-left text working. Put
it after the `INVALID_FIELDS` consumer, near line 161:

```python
def test_a_rationale_may_carry_right_to_left_text_and_bidi_marks(
    adopter_dir, write_unit, run_cli
):
    # U+200F is a bidirectional MARK, not an embedding, override or isolate:
    # it is how correct mixed Arabic and Hebrew text is written.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "‏ما هي الخطة؟"\n'
        '  options:\n    - label: "אלף"\n'
        '      disposition: chosen\n      reason: "‏السبب"\n'
        '    - label: "בית"\n      disposition: rejected\n'
        '      reason: "‏سبب آخر"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: `test_every_invalid_unit_gates_naming_unit_and_field` FAILS -- every
new tuple's expected `ERROR: knowledge/<name>.md: <field>: ` line is absent,
because after Task 1 `rationale` is simply accepted unchecked. The RTL test
PASSES already, which is correct: it is a regression guard, and it must keep
passing after Step 3.

- [ ] **Step 3: Write the implementation**

Beside `BASE_FIELDS` in `validated_memory/contract.py`:

```python
RATIONALE_FIELDS = ("question", "options")
OPTION_FIELDS = ("label", "disposition", "reason")
DISPOSITIONS = ("chosen", "rejected")
# The bidirectional embeddings, overrides, pop and isolates: they reorder
# what a reader sees without changing the string. The bidirectional MARKS --
# U+200E, U+200F, U+061C -- are deliberately absent: they resolve direction
# for mixed text and are how correct Arabic and Hebrew is written.
BIDI_CONTROLS = "‪‫‬‭‮⁦⁧⁨⁩"
```

Dispatch it in `_check_unit`, after `_check_provenance`
(`validated_memory/contract.py:85`):

```python
    findings.extend(_check_rationale(location, data))
```

And the checks themselves, beside the other `_check_*` functions:

```python
def _check_rationale(location, data):
    """The rationale envelope: closed, exactly one chosen, labels distinct.

    Absent is valid and silent: most units are measurements and record no
    choice between alternatives.
    """
    if "rationale" not in data:
        return []
    rationale = data["rationale"]
    if not isinstance(rationale, dict):
        return [
            Finding(
                ERROR,
                location,
                "rationale",
                f"{_describe(rationale)} is not a mapping",
            )
        ]

    findings = []
    for key in rationale:
        if key not in RATIONALE_FIELDS:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "rationale",
                    f"unknown key '{key}'; a rationale declares "
                    + ", ".join(RATIONALE_FIELDS),
                )
            )
    findings.extend(
        _check_rationale_text(location, "rationale.question", rationale, "question")
    )

    if "options" not in rationale:
        findings.append(
            Finding(ERROR, location, "rationale.options", "required field is missing")
        )
        return findings
    options = rationale["options"]
    if not isinstance(options, list):
        findings.append(
            Finding(
                ERROR,
                location,
                "rationale.options",
                f"{_describe(options)} is not a list",
            )
        )
        return findings
    if len(options) < 2:
        findings.append(
            Finding(
                ERROR,
                location,
                "rationale.options",
                f"a rationale declares at least two options; found {len(options)}",
            )
        )

    chosen = 0
    seen_labels = {}
    for index, option in enumerate(options):
        field = f"rationale.options[{index}]"
        if not isinstance(option, dict):
            findings.append(
                Finding(ERROR, location, field, f"{_describe(option)} is not a mapping")
            )
            continue
        for key in option:
            if key not in OPTION_FIELDS:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        field,
                        f"unknown key '{key}'; an option declares "
                        + ", ".join(OPTION_FIELDS),
                    )
                )
        findings.extend(
            _check_rationale_text(location, f"{field}.label", option, "label")
        )
        findings.extend(
            _check_rationale_text(location, f"{field}.reason", option, "reason")
        )

        if "disposition" not in option:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    f"{field}.disposition",
                    "required field is missing",
                )
            )
        elif option["disposition"] not in DISPOSITIONS:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    f"{field}.disposition",
                    f"{_describe(option['disposition'])} is not one of "
                    + ", ".join(DISPOSITIONS),
                )
            )
        elif option["disposition"] == "chosen":
            chosen += 1

        label = option.get("label")
        if isinstance(label, str) and label.strip():
            # Compared after collapsing whitespace: 'A' and 'A ' are
            # different strings that would draw as the same node.
            collapsed = " ".join(label.split())
            if collapsed in seen_labels:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        f"{field}.label",
                        f"collides with rationale.options[{seen_labels[collapsed]}]"
                        ".label; two options would draw as one node",
                    )
                )
            else:
                seen_labels[collapsed] = index

    if options and chosen != 1:
        findings.append(
            Finding(
                ERROR,
                location,
                "rationale.options",
                f"exactly one option is 'chosen'; found {chosen}",
            )
        )
    return findings


def _check_rationale_text(location, field, mapping, key):
    """One of the three text values: present, a non-empty string, no bidi controls."""
    if key not in mapping:
        return [Finding(ERROR, location, field, "required field is missing")]
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        return [
            Finding(
                ERROR, location, field, f"{_describe(value)} is not a non-empty string"
            )
        ]
    for char in value:
        if char in BIDI_CONTROLS:
            return [
                Finding(
                    ERROR,
                    location,
                    field,
                    f"carries the bidirectional control U+{ord(char):04X}, which "
                    "reorders what a reader sees without changing the string",
                )
            ]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: PASS, including the RTL guard.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add validated_memory/contract.py tests/test_validate.py
git commit -m "feat: validate the rationale envelope, its options and its text"
```

---

### Task 3: The quoting rule, enforced over the raw text

**Files:**
- Modify: `validated_memory/contract.py` (an `import re` if absent, one
  pattern constant, `_check_rationale_quoting`, and the `units` tuple in
  `validate_documents` at `validated_memory/contract.py:33-46` plus the two
  loops that unpack it at lines 47 and 64)
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `_check_rationale` from Task 2.
- Produces: `_check_rationale_quoting(location, text) -> list[Finding]`, whose
  findings carry a `line` number. `units` becomes a list of
  `(location, data, text)` triples inside `validate_documents`.

**Why the raw text.** The parser returns the same Python string for
`reason: "x"` and `reason: x`, so nothing downstream can tell them apart --
and in a plain scalar everything from ` #` onward is already gone
(`validated_memory/frontmatter.py:164-181`). The scan is bounded to the
`rationale` block so an anchor payload key named `reason` is never examined.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validate.py`:

```python
def test_an_unquoted_rationale_value_is_an_error_with_its_line(
    adopter_dir, write_unit, run_cli
):
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        "      reason: keep the # literal here\n"
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:9: rationale.reason: " in result.stderr


def test_an_unquoted_rationale_value_is_an_error_even_without_a_hash(
    adopter_dir, write_unit, run_cli
):
    # The rule is "quoted", not "quoted when it would lose text": a rule that
    # only fires on the character that silently truncates is a rule nobody can
    # rely on.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: Q?\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "rationale.question: " in result.stderr


def test_an_anchor_payload_key_named_reason_is_not_touched_by_the_rule(
    adopter_dir, write_unit, run_cli
):
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n  - system: adopter-repo\n"
        "    kind: git_ref\n    captured_at: 2026-08-11\n    payload:\n"
        "      reason: plain and unquoted on purpose\n",
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: the first two FAIL (exit code 0, no such ERROR line); the third
PASSES already and is the regression guard that the scan stays bounded.

- [ ] **Step 3: Write the implementation**

At the top of `validated_memory/contract.py`, with the other imports:

```python
import re
```

Beside the other constants:

```python
# A key line inside the rationale block, with or without the list dash that
# introduces an option: `      reason: "..."` and `    - label: "..."`.
RATIONALE_TEXT_LINE = re.compile(
    r"^(\s*)(?:-\s+)?(question|label|reason):[ \t]*(\S.*)$"
)
```

The scan itself:

```python
def _check_rationale_quoting(location, text):
    """The three rationale text values must be quoted in the raw frontmatter.

    Bounded to the `rationale` block: from its top-level key line to the next
    line at indent zero. Outside that region nothing is examined, so an anchor
    payload with a key named `reason` is untouched -- which a scan over the
    whole document would have flagged. Inside it, those three names can belong
    to nothing else: the envelope is closed, so any other key is already an
    ERROR from `_check_rationale`.

    Indentation rules are the tokenizer's own
    (`frontmatter._tokenize`): spaces only, tabs rejected outright, blank and
    comment-only lines skipped.
    """
    findings = []
    delimiters = 0
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and stripped == "---":
            delimiters += 1
            if delimiters == 2:
                break
            continue
        if indent == 0:
            inside = stripped == "rationale:"
            continue
        if not inside:
            continue
        match = RATIONALE_TEXT_LINE.match(line)
        if match is None:
            continue
        if match.group(3)[0] not in "\"'":
            findings.append(
                Finding(
                    ERROR,
                    location,
                    f"rationale.{match.group(2)}",
                    "value is not quoted; an unquoted scalar loses everything "
                    "from ' #' onward, before validation can see it",
                    line=number,
                )
            )
    return findings
```

Wire it into `validate_documents` so findings stay in document order. Change
the append at `validated_memory/contract.py:44`:

```python
        units.append((location, data, text))
```

and the two loops that consume it (`validated_memory/contract.py:47` and
`:64`):

```python
    declared = {}
    for location, data, text in units:
        findings.extend(_check_unit(location, data, extension))
        findings.extend(_check_rationale_quoting(location, text))
```

```python
    for location, data, _text in units:
        findings.extend(_check_supersedes(location, data, declared))
```

`_check_supersession_cycles(units, declared)` receives the same list and does
unpack it, at `validated_memory/contract.py:209`. Widen it the same way:

```python
    for _location, data, _text in units:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: PASS. The line number in the first test is 9 because the fixture
writes `---` on line 1 and the frontmatter body from line 2.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all tests pass. `tests/test_frontmatter_subset.py` is unaffected:
those fixtures fail at the parser, before the contract sees them.

- [ ] **Step 6: Commit**

```bash
git add validated_memory/contract.py tests/test_validate.py
git commit -m "feat: require rationale text values to be quoted in the source"
```

---

### Task 4: Documentation, and a test that keeps it in sync

**Files:**
- Modify: `docs/reference/curated-knowledge.md:10-62` (the `## Base contract`
  section: the canonical block at lines 14-24, plus prose for the new field
  and the quoting rule)
- Modify: `skills/create-knowledge-unit/SKILL.md:13-52` (the canonical block
  at lines 15-27 and the field-by-field list)
- Modify: `validated_memory/init.py:86-87` (the `EXTENSION_STUB` sentence that
  names the base fields)
- Create: `tests/test_contract_docs.py`

**Interfaces:**
- Consumes: `BASE_FIELDS` from Task 1, read as text -- the test does not
  import the package.
- Produces: the marker `<!-- canonical-base-contract -->`, which precedes each
  block that enumerates the whole contract.

**Why only two blocks.** Exactly two places enumerate the whole contract:
`docs/reference/curated-knowledge.md:14-24` and
`skills/create-knowledge-unit/SKILL.md:15-27`. The three other places that show
a unit -- `README.md:88-103`, `docs/walkthrough.md:54-69` and
`docs/walkthrough.md:129-146` -- are partial by design: none carries
`provenance`, because none of those examples needs it. Comparing them against
`BASE_FIELDS` would force rewriting examples that are correct as they are.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contract_docs.py`:

```python
"""The canonical contract enumerations must name exactly the base fields.

Same seam as `test_skills_structure.py`: this reads shipped content and never
imports the package's internals -- `BASE_FIELDS` is read out of the source as
text.

Two documents enumerate the whole base contract, and both are marked with
`<!-- canonical-base-contract -->`. The three other places that show a unit
(`README.md` and two blocks in `docs/walkthrough.md`) are partial by design:
none carries `provenance`. They are excluded by name, here, so the difference
is written down instead of guessed.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- canonical-base-contract -->"
CANONICAL = (
    REPO_ROOT / "docs" / "reference" / "curated-knowledge.md",
    REPO_ROOT / "skills" / "create-knowledge-unit" / "SKILL.md",
)
PARTIAL_BY_DESIGN = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "walkthrough.md",
)
MARKED_BLOCK = re.compile(
    re.escape(MARKER) + r"\s*\n```yaml\n(.*?)\n```", re.DOTALL
)
TOP_LEVEL_KEY = re.compile(r"^([a-z_]+):", re.MULTILINE)


def _base_fields():
    source = (REPO_ROOT / "validated_memory" / "contract.py").read_text(
        encoding="utf-8"
    )
    declaration = re.search(r"^BASE_FIELDS = \((.*?)\)", source, re.DOTALL | re.MULTILINE)
    assert declaration, "BASE_FIELDS is no longer a parenthesised tuple literal"
    return set(re.findall(r'"([a-z_]+)"', declaration.group(1)))


def test_every_canonical_block_names_exactly_the_base_contract():
    fields = _base_fields()
    assert fields, "no BASE_FIELDS found"

    for path in CANONICAL:
        text = path.read_text(encoding="utf-8")
        blocks = MARKED_BLOCK.findall(text)
        assert blocks, f"{path} carries no {MARKER} block"
        for block in blocks:
            assert set(TOP_LEVEL_KEY.findall(block)) == fields, (
                f"{path}: the canonical block names "
                f"{sorted(set(TOP_LEVEL_KEY.findall(block)))}, "
                f"the contract declares {sorted(fields)}"
            )


def test_the_partial_examples_are_not_marked_canonical():
    for path in PARTIAL_BY_DESIGN:
        assert MARKER not in path.read_text(encoding="utf-8"), (
            f"{path} holds examples that legitimately omit optional fields; "
            "marking it canonical would force rewriting them"
        )


def test_the_extension_stub_names_every_base_field():
    stub = (REPO_ROOT / "validated_memory" / "init.py").read_text(encoding="utf-8")
    prose = re.search(r"EXTENSION_STUB = \"\"\"(.*?)\"\"\"", stub, re.DOTALL)
    assert prose, "EXTENSION_STUB is no longer a triple-quoted string"
    for field in _base_fields():
        assert f"`{field}`" in prose.group(1), (
            f"the extension stub does not mention the base field '{field}'"
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_contract_docs.py -v`
Expected: `test_every_canonical_block_names_exactly_the_base_contract` FAILS
with "carries no `<!-- canonical-base-contract -->` block";
`test_the_extension_stub_names_every_base_field` FAILS because the stub does
not mention `rationale`.

- [ ] **Step 3: Update the two canonical blocks and the stub**

In `docs/reference/curated-knowledge.md`, put the marker on the line before
the fenced block at line 14 and add the field:

```markdown
<!-- canonical-base-contract -->
```

```yaml
id: <stable-unique-id>          # required; letters, digits, '.', '_', '-'
evidence: measured              # required; measured | verifiable | hypothesis
supersedes: []                  # optional; ids this unit supersedes (many-to-one)
anchors:                        # optional; without anchors a unit cannot expire
  - system: <system-name>       # complete envelope: all four fields required
    kind: git_ref               # probe discriminator; no whitespace
    captured_at: 2026-08-11T10:00:00Z   # ISO-8601 date or timestamp
    payload: {}                 # mapping; interpreted by the probe, not here
provenance: []                  # optional; where the native artifact lives
rationale:                      # optional; how this conclusion was chosen
  question: "..."               # required inside rationale; quoted
  options:                      # at least two; exactly one 'chosen'
    - label: "..."              # quoted
      disposition: chosen       # chosen | rejected
      reason: "..."             # quoted
```

Then add prose under the block, in the same voice as the rest of the section:

```markdown
`rationale` records how a conclusion was chosen: the question, the options
considered, which one was taken and why each was taken or left. It holds no
reference to another unit, so it adds no relation and cannot form a cycle:
`supersedes` remains the only relation between units. A rejected option is not
false and not superseded -- it was considered and not chosen, here.

**The three text values are quoted.** In a plain scalar the frontmatter subset
treats ` #` as the start of a comment, so `reason: keep the # here` becomes
`keep the` before anything validates it. `validate` therefore rejects an
unquoted `question`, `label` or `reason`, quoted or not. Prefer `"` and fall
back to `'` when the text contains a double quote; text containing both has no
representation, because quoted scalars admit no backslash escapes. Each value
is one line: long-form argument belongs in the unit body or in `provenance`.
```

Make the same two edits in `skills/create-knowledge-unit/SKILL.md`: the marker
and the field in the block at lines 15-27, and an entry for `rationale` in the
field-by-field list, stating the quoting rule.

In `validated_memory/init.py:86-87`, extend the sentence that names the base
fields:

```python
fields the adopter's units may carry, on top of the base contract (`id`, `evidence`,
`supersedes`, `anchors`, `provenance`, `rationale`). No fields are declared yet
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_contract_docs.py -v`
Expected: PASS, all three tests.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all tests pass. `tests/test_init.py:66-82` reads the stub's text but
does not pin that sentence, so extending it is safe; if it goes red, the test
is pinning prose and its assertion moves to the new sentence.

- [ ] **Step 6: Commit**

```bash
git add docs/reference/curated-knowledge.md skills/create-knowledge-unit/SKILL.md validated_memory/init.py tests/test_contract_docs.py
git commit -m "docs: document rationale and pin the canonical contract blocks"
```

---

## Done when

- `python3 -m pytest` is green.
- A unit carrying a well-formed `rationale` validates clean; every structural
  defect in the spec's list is an ERROR naming its exact field; an unquoted
  `question`, `label` or `reason` is an ERROR carrying its line number.
- Legitimate right-to-left text passes.
- The two canonical documentation blocks and the extension stub name exactly
  the six base fields, and a test says so.
- Nothing renders `rationale` yet. That is plan 2.
