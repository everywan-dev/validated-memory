"""End-to-end tests for `validate` against the base contract.

Fixtures are synthetic adopter trees: a `knowledge/` directory holding curated
knowledge units, each a Markdown file whose frontmatter carries the contract.
"""

import pytest

VALID_UNIT = """\
id: kb-0001
evidence: measured
anchors:
  - system: adopter-repo
    kind: git_ref
    captured_at: 2026-08-11T10:00:00Z
    payload:
      repo: .
      ref: refs/heads/main
provenance:
  - docs/measurements/run-2026-08-11.md
"""

SUPERSEDING_UNIT = """\
id: kb-0002
evidence: hypothesis
supersedes:
  - kb-0001
anchors:
  - system: adopter-repo
    kind: git_ref
    captured_at: 2026-08-11
    payload: {}
"""

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


def test_valid_units_pass_clean(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", VALID_UNIT)
    write_unit("kb-0002.md", SUPERSEDING_UNIT)

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
    assert "WARNING" not in result.stderr
    assert "2" in result.stdout


def test_units_are_found_recursively(adopter_dir, write_unit, run_cli):
    write_unit("nested/deep/kb-0001.md", VALID_UNIT)

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout


def test_explicit_path_overrides_the_default_directory(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", VALID_UNIT)
    (adopter_dir / "knowledge").rename(adopter_dir / "facts")

    result = run_cli("validate", "facts", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr


# --- base contract: field-level errors ---------------------------------------

INVALID_FIELDS = [
    (
        "evidence_out_of_domain",
        "id: kb-0001\nevidence: probable\n",
        "evidence",
    ),
    (
        "evidence_missing",
        "id: kb-0001\n",
        "evidence",
    ),
    (
        "id_missing",
        "evidence: measured\n",
        "id",
    ),
    (
        "id_not_a_string",
        "id:\n  - kb-0001\nevidence: measured\n",
        "id",
    ),
    (
        "unknown_top_level_field",
        "id: kb-0001\nevidence: measured\nseverity: high\n",
        "severity",
    ),
    (
        "anchor_envelope_incomplete",
        (
            "id: kb-0001\nevidence: measured\n"
            "anchors:\n  - system: repo\n    kind: git_ref\n"
        ),
        "anchors[0]",
    ),
    (
        "anchor_unknown_field",
        (
            "id: kb-0001\nevidence: measured\n"
            "anchors:\n  - system: repo\n    kind: git_ref\n"
            "    captured_at: 2026-08-11\n    payload: {}\n    extra: nope\n"
        ),
        "anchors[0]",
    ),
    (
        "anchor_captured_at_not_iso",
        (
            "id: kb-0001\nevidence: measured\n"
            "anchors:\n  - system: repo\n    kind: git_ref\n"
            "    captured_at: yesterday\n    payload: {}\n"
        ),
        "anchors[0].captured_at",
    ),
    (
        "anchor_captured_at_not_a_real_date",
        (
            "id: kb-0001\nevidence: measured\n"
            "anchors:\n  - system: repo\n    kind: git_ref\n"
            "    captured_at: 2026-02-31\n    payload: {}\n"
        ),
        "anchors[0].captured_at",
    ),
    (
        "anchor_payload_not_a_mapping",
        (
            "id: kb-0001\nevidence: measured\n"
            "anchors:\n  - system: repo\n    kind: git_ref\n"
            "    captured_at: 2026-08-11\n    payload: []\n"
        ),
        "anchors[0].payload",
    ),
    (
        "anchors_not_a_list",
        "id: kb-0001\nevidence: measured\nanchors:\n  system: repo\n",
        "anchors",
    ),
    (
        "provenance_not_a_list",
        "id: kb-0001\nevidence: measured\nprovenance: docs/source.md\n",
        "provenance",
    ),
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
        '  options:\n    - label: "A\u202eB"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
        "rationale.options[0].label",
    ),
]


def test_every_invalid_unit_gates_naming_unit_and_field(adopter_dir, write_unit, run_cli):
    for name, frontmatter, _field in INVALID_FIELDS:
        write_unit(f"{name}.md", frontmatter)

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    for name, _frontmatter, field in INVALID_FIELDS:
        expected = f"ERROR: knowledge/{name}.md: {field}: "
        assert expected in result.stderr, (
            f"{name}: missing finding for field '{field}'\n{result.stderr}"
        )


def test_an_empty_options_list_is_one_finding_naming_the_count(
    adopter_dir, write_unit, run_cli
):
    # An empty list is falsy, so `if options and chosen != 1` never fires
    # alongside the "too few" check: one finding, not two, for one defect.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q"\n'
        '  options: []\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert result.stderr.count("rationale.options: ") == 1
    line = next(
        text_line
        for text_line in result.stderr.splitlines()
        if "rationale.options: " in text_line
    )
    assert "at least two options; found 0" in line


def test_a_rationale_may_carry_right_to_left_text_and_bidi_marks(
    adopter_dir, write_unit, run_cli
):
    # U+200F is a bidirectional MARK, not an embedding, override or isolate:
    # it is how correct mixed Arabic and Hebrew text is written.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "\u200fما هي الخطة؟"\n'
        '  options:\n    - label: "אלף"\n'
        '      disposition: chosen\n      reason: "\u200fالسبب"\n'
        '    - label: "בית"\n      disposition: rejected\n'
        '      reason: "\u200fسبب آخر"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "rationale" not in result.stderr


BIDI_CONTROL_CODEPOINTS = [
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
]


@pytest.mark.parametrize(
    "control",
    BIDI_CONTROL_CODEPOINTS,
    ids=[f"U+{ord(c):04X}" for c in BIDI_CONTROL_CODEPOINTS],
)
def test_each_bidi_control_inside_a_quoted_question_is_an_error(
    control, adopter_dir, write_unit, run_cli
):
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        f'  question: "Q{control}?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "rationale.question: " in result.stderr
    assert f"U+{ord(control):04X}" in result.stderr


@pytest.mark.parametrize(
    "mark", ['\u200e', '\u061c'], ids=["U+200E", "U+061C"]
)
def test_further_bidi_marks_pass_clean_beside_the_rtl_guard(
    mark, adopter_dir, write_unit, run_cli
):
    # These two marks, alongside U+200F above, are how correct
    # mixed-direction text is written -- not embeddings, overrides or
    # isolates -- so the contract passes them through untouched.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        f'  question: "{mark}Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "rationale" not in result.stderr


# --- rationale quoting: enforced over the raw text ---------------------------


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
    # The rule is "quoted", not "quoted when it would lose text": a rule
    # that only fires on the character that silently truncates is a rule
    # nobody can rely on.
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


def test_an_unquoted_value_is_an_error_with_a_space_before_the_colon(
    adopter_dir, write_unit, run_cli
):
    # The parser accepts a space before the colon (the key is
    # key.strip() after partition(":")), so the quoting scan has to
    # accept it too.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question : Q?\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:5: rationale.question: " in result.stderr


def test_an_unquoted_value_is_an_error_when_the_block_key_has_a_space_before_its_colon(
    adopter_dir, write_unit, run_cli
):
    # `rationale :` is the same key line as `rationale:` to the parser,
    # so it has to open the same scanning region.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale :\n  question: Q?\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:5: rationale.question: " in result.stderr


def test_an_unquoted_value_is_an_error_when_the_block_key_ends_in_a_comment(
    adopter_dir, write_unit, run_cli
):
    # `frontmatter._cut_comment` returns "" for a remainder that starts with
    # "#", so the parser takes `rationale:#comment` as the same block key
    # line as `rationale:`; the scan has to open the region on it too.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:#comment\n  question: Q?\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:5: rationale.question: " in result.stderr
    assert "rationale.options" not in result.stderr


def test_an_unquoted_value_is_an_error_after_a_non_breaking_space(
    adopter_dir, write_unit, run_cli
):
    # The parser's own value.strip() treats a non-breaking space the
    # same as an ASCII space, so a value that starts right after one is
    # still unquoted.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        "      reason:\u00a0R\n    - label: \"B\"\n      disposition: rejected\n"
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:9: rationale.reason: " in result.stderr


def test_an_unquoted_label_written_as_a_list_item_is_an_error(
    adopter_dir, write_unit, run_cli
):
    # The only test of the pattern's list-dash branch: an option's
    # opening line, `    - label: ...`, rather than a plain key: value
    # line.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        "  options:\n    - label: A\n      disposition: chosen\n"
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md:7: rationale.label: " in result.stderr


def test_an_anchor_payload_key_named_reason_is_not_touched_by_the_rule(
    adopter_dir, write_unit, run_cli
):
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n'
        "anchors:\n  - system: adopter-repo\n"
        "    kind: git_ref\n    captured_at: 2026-08-11\n    payload:\n"
        "      reason: plain and unquoted on purpose\n",
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "rationale." not in result.stderr


def test_the_closing_fence_stops_the_scan_before_the_document_body(
    adopter_dir, write_unit, run_cli
):
    # The document body, after the closing "---", is not frontmatter at all
    # -- even when it happens to look exactly like an unquoted rationale
    # block starting at column 0.
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n  question: "Q?"\n'
        '  options:\n    - label: "A"\n      disposition: chosen\n'
        '      reason: "R"\n    - label: "B"\n      disposition: rejected\n'
        '      reason: "R"\n',
        body="rationale:\n  question: unquoted in the body\n",
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "rationale." not in result.stderr


def test_missing_frontmatter_is_an_error(adopter_dir, run_cli):
    path = adopter_dir / "knowledge" / "no-frontmatter.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Just a document\n", encoding="utf-8")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "no-frontmatter.md" in result.stderr
    assert "frontmatter" in result.stderr


def test_a_contract_finding_names_the_unit_and_field_without_a_line(
    adopter_dir, write_unit, run_cli
):
    # A structural rule speaks about the unit as a whole, so it carries no
    # line; the parser and the rationale quoting scan, which read the raw
    # text, do carry one (see the frontmatter tests and the quoting tests).
    write_unit("kb-0001.md", "id: kb-0001\nanchors: []\n")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert (
        "ERROR: knowledge/kb-0001.md: evidence: required field is missing"
        in result.stderr
    )


# --- base contract: cross-unit errors ----------------------------------------


def test_duplicate_id_gates_naming_both_units(adopter_dir, write_unit, run_cli):
    write_unit("first.md", VALID_UNIT)
    write_unit("second.md", VALID_UNIT)

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "duplicate id 'kb-0001'" in result.stderr
    assert "knowledge/first.md" in result.stderr
    assert "knowledge/second.md" in result.stderr


def test_supersedes_pointing_at_a_missing_id_gates(adopter_dir, write_unit, run_cli):
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0404\nanchors: []\n",
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0002.md: supersedes: " in result.stderr
    assert "kb-0404" in result.stderr


def test_self_supersession_gates(adopter_dir, write_unit, run_cli):
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nsupersedes:\n  - kb-0001\nanchors: []\n",
    )

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "supersedes itself" in result.stderr


def test_supersession_across_files_resolves_in_any_order(
    adopter_dir, write_unit, run_cli
):
    # The superseding unit sorts before the unit it supersedes.
    write_unit(
        "aaa.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\nanchors: []\n",
    )
    write_unit("zzz.md", "id: kb-0001\nevidence: measured\nanchors: []\n")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr


# --- WARNING does not gate; ERROR does ---------------------------------------


def test_a_unit_without_anchors_warns_without_gating(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: knowledge/kb-0001.md: anchors: " in result.stderr
    assert "1 warning(s)" in result.stdout


def test_an_empty_anchor_list_warns_without_gating(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\nanchors: []\n")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr


def test_warnings_and_errors_are_reported_apart(adopter_dir, write_unit, run_cli):
    write_unit("warned.md", "id: kb-0001\nevidence: measured\n")
    write_unit("failed.md", "id: kb-0002\nevidence: probable\nanchors: []\n")

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "1 error(s)" in result.stdout
    assert "2 warning(s)" in result.stdout


# --- target resolution --------------------------------------------------------


def test_a_missing_default_directory_gates_and_points_at_init(adopter_dir, run_cli):
    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "knowledge" in result.stderr
    assert "init" in result.stderr


def test_a_missing_explicit_path_gates(adopter_dir, run_cli):
    result = run_cli("validate", "nowhere", cwd=adopter_dir)

    assert result.returncode == 1
    assert "nowhere" in result.stderr


def test_an_empty_knowledge_directory_warns_without_gating(adopter_dir, run_cli):
    (adopter_dir / "knowledge").mkdir()

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr


def test_a_single_file_can_be_validated(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", VALID_UNIT)
    write_unit("kb-0002.md", "id: kb-0002\nevidence: probable\nanchors: []\n")

    result = run_cli("validate", "knowledge/kb-0001.md", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "1 unit(s) checked" in result.stdout


def test_supersession_resolves_against_the_validated_set_only(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\nanchors: []\n")
    write_unit("kb-0002.md", SUPERSEDING_UNIT)

    result = run_cli("validate", "knowledge/kb-0002.md", cwd=adopter_dir)

    assert result.returncode == 1
    assert "does not exist in the validated set" in result.stderr


# --- supersession cycles -----------------------------------------------------


def _unit(unit_id, supersedes):
    return f"id: {unit_id}\nevidence: measured\nsupersedes:\n  - {supersedes}\n"


def test_a_two_unit_supersession_cycle_gates(adopter_dir, write_unit, run_cli):
    # Both are superseded, so neither is live: the pair vanishes from the
    # index's active view and `probe` stops probing it, since only active
    # units are probed. Nothing was deleted, yet nothing is checked either.
    write_unit("kb-0001.md", _unit("kb-0001", "kb-0002"))
    write_unit("kb-0002.md", _unit("kb-0002", "kb-0001"))

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "supersession cycle" in result.stderr
    assert "kb-0001" in result.stderr
    assert "kb-0002" in result.stderr


def test_a_longer_supersession_cycle_gates(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", _unit("kb-0001", "kb-0002"))
    write_unit("kb-0002.md", _unit("kb-0002", "kb-0003"))
    write_unit("kb-0003.md", _unit("kb-0003", "kb-0001"))

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "supersession cycle" in result.stderr
    assert result.stderr.count("supersession cycle") == 1


def test_a_supersession_chain_that_ends_is_clean(adopter_dir, write_unit, run_cli):
    # kb-0003 supersedes kb-0002 supersedes kb-0001: a chain with a live end.
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    write_unit("kb-0002.md", _unit("kb-0002", "kb-0001"))
    write_unit("kb-0003.md", _unit("kb-0003", "kb-0002"))

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "cycle" not in result.stderr


def test_self_supersession_is_not_also_reported_as_a_cycle(
    adopter_dir, write_unit, run_cli
):
    # A unit pointing at itself already has its own ERROR; a self-loop is a
    # cycle of one, and reporting both would say the same thing twice.
    write_unit("kb-0001.md", _unit("kb-0001", "kb-0001"))

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert "supersedes itself" in result.stderr
    assert "cycle" not in result.stderr


def test_two_separate_cycles_are_both_reported(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", _unit("kb-0001", "kb-0002"))
    write_unit("kb-0002.md", _unit("kb-0002", "kb-0001"))
    write_unit("kb-0003.md", _unit("kb-0003", "kb-0004"))
    write_unit("kb-0004.md", _unit("kb-0004", "kb-0003"))

    result = run_cli("validate", cwd=adopter_dir)

    assert result.returncode == 1
    assert result.stderr.count("supersession cycle") == 2
