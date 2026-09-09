"""Structural checks for the `consult-project-memory` skill.

Same seam as `test_skills_structure.py`: this reads shipped Markdown content
and never imports `validated_memory`.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skills" / "consult-project-memory" / "SKILL.md"

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _text():
    return SKILL.read_text(encoding="utf-8")


def _frontmatter():
    match = FRONTMATTER_PATTERN.match(_text())
    assert match, "SKILL.md has no '---' frontmatter block"
    return match.group(1)


def test_frontmatter_name_and_description_are_single_line():
    frontmatter = _frontmatter()
    name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
    assert name_match and name_match.group(1).strip() == "consult-project-memory"
    description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    assert description_match, "frontmatter has no 'description'"
    description = description_match.group(1).strip()
    assert description, "description is empty"
    assert "explicit request" in description
    assert "opted into" in description
    assert "not triggered automatically" in description
    # A folded multiline YAML scalar (">" or "|") is not the existing style;
    # every other skill's description is one physical line.
    assert not description.startswith((">", "|")), (
        "description uses folded/literal YAML block style, not the plain "
        "single-line style every other skill uses"
    )
    # No frontmatter key may reappear below the description on its own line
    # (a sign the description silently continued as a YAML block scalar).
    lines = frontmatter.splitlines()
    description_index = lines.index(
        next(line for line in lines if line.startswith("description:"))
    )
    for line in lines[description_index + 1 :]:
        assert not line.startswith(" "), (
            f"description appears to continue onto an indented line: {line!r}"
        )


def test_invocation_is_plugin_resolved_and_writes_no_bytecode():
    text = _text()
    invocation = (
        'PYTHONDONTWRITEBYTECODE=1 '
        'PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" '
        "python3 -P -m validated_memory recall"
    )
    assert invocation in text, (
        "the skill must invoke recall through the plugin-resolved, "
        "no-bytecode command line"
    )
    # No bare `-B` variant and no invocation that skips the PYTHONPATH pin.
    assert "-B -m validated_memory" not in text
    assert "python3 -m validated_memory" not in text.replace(
        "python3 -P -m validated_memory", ""
    )


def test_names_the_four_exit_code_outcomes():
    text = _text()
    assert "four outcomes" in text
    for needle in (
        "matched",
        "omitted",
        "complete",
        "usage error",
    ):
        assert needle in text, f"exit-code guidance no longer mentions {needle!r}"
    assert "raise" in text.lower() or "narrow" in text.lower()


def test_states_consult_first_is_not_trust_first():
    text = _text()
    assert "hypothesis" in text.lower()
    assert "not trusting first" in text.lower() or "not trust-first" in text.lower()


def test_names_only_the_real_evidence_states():
    text = _text()
    for state in ("measured", "verifiable", "hypothesis"):
        assert state in text, f"the skill no longer names the {state!r} state"
    # `verified` is a verdict-shaped word no unit can declare: the evidence
    # states are exactly the three above, and an agent told to look for
    # `verified` would look for something the CLI never emits.
    assert "verified" not in text, (
        "the skill names 'verified', which is not one of the evidence states"
    )


def test_zero_matches_is_a_lexical_result_not_a_conclusion():
    text = _normalized()
    assert "not a conclusion that nothing relevant exists" in text
    assert "ordinary source search" in text


def _normalized():
    return " ".join(_text().split())


def test_says_there_is_no_gate_and_that_an_adopter_may_require_it():
    text = _normalized()
    # Two facts, no meta-commentary: the plugin enforces nothing on its own,
    # and an adopter's own instruction file is where such a rule can live.
    assert "no automatic consultation gate" in text
    assert "can still require the procedure" in text
    assert "adopter's own instruction file" in text


def test_fallback_stays_inside_authorized_scope():
    text = _text()
    assert "authorized scope" in text or "already authorized to" in text


def test_names_the_completed_p4_disposition_with_no_savings_claim():
    text = _normalized()
    assert "P4" in text
    assert "the benefit gate was not met" in text
    assert "deferred" in text
    assert "No measured time or context savings are" in text
    assert "usefulness-report.md" in text


def test_two_triggers_scope_before_work_cadence_to_opt_in():
    text = _normalized()
    assert "An explicit request" in text
    assert "A project that has opted into a consultation-first workflow" in text
    assert (
        "Absent one of these two triggers, this is not a step to perform "
        "automatically on every substantial task." in text
    )
