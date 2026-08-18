"""End-to-end tests for the `render` subcommand."""

import json
import re
import shutil

HISTORY_WINDOW = 20


def _log(adopter_dir, records):
    (adopter_dir / "verdicts.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _scaffold(run_cli, adopter_dir, write_unit):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\n",
        "# The first conclusion\n\nSupporting prose.\n",
    )


def test_render_writes_the_knowledge_page(run_cli, adopter_dir, write_unit):
    _scaffold(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page
    assert "1 unit(s) under knowledge/" in page
    assert "render: wrote knowledge.html" in result.stdout


def test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical(
    run_cli, adopter_dir, write_unit
):
    _scaffold(run_cli, adopter_dir, write_unit)
    run_cli("render", cwd=adopter_dir)
    first = (adopter_dir / "knowledge.html").read_bytes()

    stamp = (adopter_dir / "knowledge.html").stat().st_mtime_ns

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0
    assert "render: unchanged knowledge.html" in result.stdout
    assert (adopter_dir / "knowledge.html").read_bytes() == first
    # Identical bytes alone would pass an implementation that rewrites the
    # same content and prints `unchanged`. The file must not be touched.
    assert (adopter_dir / "knowledge.html").stat().st_mtime_ns == stamp


def test_an_error_finding_stops_the_run_and_writes_nothing(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


URL_BEARING = {"src", "srcset", "data", "poster", "action", "formaction",
               "cite", "background", "xlink:href", "ping"}


def test_every_unit_has_a_section_with_its_headline_and_grades(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: hypothesis\n",
        "# A claim worth checking\n\nProse.\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    sections = [
        attrs for tag, attrs in page_elements(page)
        if tag == "section" and attrs.get("class") == "unit"
    ]

    assert [attrs["data-unit"] for attrs in sections] == ["kb-0001"]
    assert sections[0]["data-state"] == "active"
    assert "A claim worth checking" in page
    assert "hypothesis" in page


def test_a_unit_without_a_heading_falls_back_to_its_id(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "Just prose.\n")

    run_cli("render", cwd=adopter_dir)

    assert "kb-0001" in (adopter_dir / "knowledge.html").read_text(encoding="utf-8")


def test_hostile_content_never_becomes_live_markup(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - 'a \"quoted\" source'\n",
        "# Title\n\n<script>alert(1)</script>\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]


def test_only_an_anchor_href_ever_carries_an_external_url(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - https://example.invalid/doc\n",
        "# Title\n\nProse.\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    for tag, attrs in elements:
        for name, value in attrs.items():
            carries_url = name in URL_BEARING or "://" in (value or "")
            if carries_url:
                assert (tag, name) == ("a", "href"), f"{tag}[{name}]={value}"
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"
    assert any(
        tag == "a" and attrs.get("href") == "https://example.invalid/doc"
        for tag, attrs in elements
    )


def test_a_hostile_provenance_scheme_is_text_and_never_a_link(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - 'javascript:alert(1)'\n",
        "# Title\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    # The contract validates nothing about a provenance entry, and escaping
    # does not neutralise a scheme -- only the link would arm it.
    assert "javascript:alert(1)" in page
    assert not [
        attrs for tag, attrs in page_elements(page)
        if tag == "a" and attrs.get("href", "").startswith("javascript:")
    ]


def test_a_hostile_anchor_payload_is_text_and_never_live_markup(
    run_cli, adopter_dir, write_unit, page_elements
):
    # `payload` is a mapping the contract checks is present as one and never
    # looks inside: the probe interprets its contents, not the contract. So
    # arbitrary structure -- here, markup nested two levels deep -- reaches
    # the page from inside the otherwise-validated layer.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: gitlab\n"
        "    kind: file-hash\n"
        "    captured_at: 2026-08-01T00:00:00Z\n"
        "    payload:\n"
        '      note: "<script>alert(1)</script>"\n',
        "# Title\n",
    )

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert "gitlab" in page
    assert "file-hash" in page
    assert "2026-08-01T00:00:00Z" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]


def test_a_verdict_log_record_that_is_not_json_is_reported_not_raised(
    run_cli, adopter_dir, write_unit
):
    # `knowledge_view.build` reads the service view through the same log
    # `derive` reads. A log it cannot parse is a finding naming the file and
    # line, not a traceback: the person opening this page has no repository
    # to read a stack trace against.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    (adopter_dir / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "verdicts.jsonl:1" in result.stderr
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_a_verdict_log_that_is_not_utf8_is_reported_without_a_line_number(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    (adopter_dir / "verdicts.jsonl").write_bytes(b"\xff\xfe not utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    # No line number: the fault is the file's, not one line's -- rendered as
    # `verdicts.jsonl: log: …`, never `verdicts.jsonl:<N>: log: …`.
    assert "verdicts.jsonl: log:" in result.stderr
    assert not re.search(r"verdicts\.jsonl:\d", result.stderr)
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_the_history_window_shows_twenty_and_states_the_true_total(
    run_cli, adopter_dir, write_unit
):
    # 25 probes of one anchor: only the most recent 20 are shown, newest
    # first, but the log's total and the anchor's own total both count all 25.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2025-12-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": f"2026-01-{day:02d}T00:00:00Z"}
        for day in range(1, 26)
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('class="record"') == HISTORY_WINDOW
    assert "25 record(s)" in page
    assert "of which 25 belong to an anchor shown below" in page
    assert "25 record(s) for this anchor; showing 20." in page
    assert "2026-01-25T00:00:00Z" in page
    assert "2026-01-01T00:00:00Z" not in page


def test_a_record_without_a_payload_is_never_attributed_to_an_anchor(
    run_cli, adopter_dir, write_unit
):
    # The rule that matters most: a record written before payloads were
    # recorded carries no `payload` field at all, and NO anchor reads it --
    # not even one whose own payload happens to be empty (`{}`), because
    # `{}` and "absent" are different keys. The record still counts toward
    # the log's total, but not toward the anchor's, and the anchor it would
    # have matched on `(system, kind)` alone stays `unknown`.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('class="record"') == 0
    assert "1 record(s)" in page
    assert "of which 0 belong to an anchor shown below" in page
    assert "0 record(s) for this anchor; showing 0." in page
    assert '<span class="verdict">unknown</span>' in page


def test_a_record_whose_payload_is_null_stops_render_and_writes_nothing(
    run_cli, adopter_dir, write_unit
):
    # `_group_history` treats "no `payload` key" as "predates payloads" and
    # skips it -- safe only because `service_view()` (through `_keyed`)
    # rejects an explicit `payload: null` before grouping ever sees a
    # record. `derive` already pins this rule for its own read of the log
    # (`tests/test_derive.py`); this pins it for `render`'s, which reads the
    # log through a separate call of its own.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        '{"unit": "kb-0001", "system": "repo", "kind": "git_ref", '
        '"payload": null, "verdict": "current", '
        '"recorded_at": "2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "verdicts.jsonl:1" in result.stderr
    assert "'payload' field is not a mapping" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_superseded_unit_appears_only_inside_its_successor(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Old\n")
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# New\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    sections = [
        attrs for tag, attrs in page_elements(page)
        if tag == "section" and attrs.get("class") in {"unit", "unit superseded"}
    ]

    top = [a for a in sections if a.get("class") == "unit"]
    assert [a["data-unit"] for a in top] == ["kb-0002"]
    assert any(
        a["data-unit"] == "kb-0001" and a["data-state"] == "superseded by kb-0002"
        for a in sections
    )


def test_a_unit_superseded_twice_is_rendered_once_and_referenced_after(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Old\n")
    for new in ("kb-0002", "kb-0003"):
        write_unit(
            f"{new}.md",
            f"id: {new}\nevidence: measured\nsupersedes:\n  - kb-0001\n",
            f"# {new}\n",
        )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('data-unit="kb-0001"') == 1
    assert '<a href="#unit-kb-0001">' in page


def test_the_memory_page_lists_entries_with_their_references(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
                 "Related: [[tea]].\n")
    write_memory("tea.md", "name: tea\ndescription: green\nmetadata:\n  type: user\n")
    write_index("- [Coffee](coffee.md) — oat milk\n- [Tea](tea.md) — green\n")

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    entries = [attrs for tag, attrs in page_elements(page)
               if tag == "section" and attrs.get("class") == "entry"]

    assert result.returncode == 0, result.stderr
    assert [attrs["data-name"] for attrs in entries] == ["coffee", "tea"]
    assert '<a href="#entry-tea">' in page
    assert "render: wrote memory.html" in result.stdout


def test_an_unresolved_wikilink_is_marked_rather_than_linked(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
                 "Related: [[nothing-here]].\n")
    write_index("- [Coffee](coffee.md) — oat milk\n")

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert 'class="unresolved"' in page
    assert '<a href="#entry-nothing-here">' not in page


def test_a_missing_memory_index_stops_the_run(run_cli, adopter_dir, write_unit):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory" / "MEMORY.md").unlink()

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "MEMORY.md" in result.stderr
    # Both artifacts are built before either is written: a memory-layer
    # precondition failing must not leave a knowledge.html written from a
    # run that, as a whole, did not succeed.
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_missing_knowledge_directory_stops_the_run_and_writes_nothing(
    run_cli, adopter_dir
):
    # The mirror case: a curated-layer precondition failing must not leave a
    # memory.html written either. `render` does not render half a project
    # quietly.
    run_cli("init", cwd=adopter_dir)
    shutil.rmtree(adopter_dir / "knowledge")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_memory_file_with_unparseable_frontmatter_is_rendered_not_raised(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # There is no `gated_source` for the memory layer: `render` stops only on
    # what it cannot read (the directory, the index). A document whose
    # frontmatter will not parse is still one of the files that directory
    # holds, so it gets an entry, with the parse error stated in place of the
    # fields that could not be read -- not a traceback, and not silence.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("broken.md", "name coffee\n")
    write_index("- [Broken](broken.md) — bad frontmatter\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    assert "broken" in page
    assert "expected a 'key: value' entry" in page


def test_a_non_string_description_does_not_raise(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # Nothing validated this layer's frontmatter, so `description` can be any
    # JSON type the parser accepts -- here, a list rather than the string
    # `lint` requires. The page must stringify it, not crash trying to
    # `.strip()` or membership-test a value of unknown type.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "coffee.md",
        "name: coffee\ndescription:\n  - oat\n  - milk\nmetadata:\n  type: user\n",
    )
    write_index("- [Coffee](coffee.md) — a list description\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    assert "oat" in page and "milk" in page


def test_hostile_memory_content_never_becomes_live_markup(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `memory_view` renders the same kind of adopter-authored freeform text
    # as `knowledge_view` (body, description, metadata), so it carries the
    # same injection risk -- mirrors the curated layer's hostile-content and
    # URL-whitelist tests above, over `memory.html` instead.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "coffee.md",
        'name: coffee\ndescription: \'a "quoted" <tag>\'\nmetadata:\n  type: user\n',
        "# Title\n\n<script>alert(1)</script>\n",
    )
    write_index("- [Coffee](coffee.md) — hostile\n")

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'a "quoted" &lt;tag&gt;' in page
    assert "user" in page

    elements = page_elements(page)
    assert not [tag for tag, _ in elements if tag == "script"]
    for tag, attrs in elements:
        for name, value in attrs.items():
            carries_url = name in URL_BEARING or "://" in (value or "")
            if carries_url:
                assert (tag, name) == ("a", "href"), f"{tag}[{name}]={value}"
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"


def test_an_outgoing_href_matches_a_spaced_and_punctuated_name_anchor(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # Unlike a curated unit's `id`, nothing constrains a memory's `name`: a
    # real corpus (ADR 0001) has names shaped like titles, with spaces, dots,
    # capitals and parentheses. The anchor (`id="entry-<name>"`) and a
    # resolved wikilink's `href` are built by the same escaping call on the
    # same string, so they should agree byte for byte -- verified here
    # rather than assumed.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "release-owner.md",
        "name: 'Release owner (approved)'\n"
        "description: who owns releases\nmetadata:\n  type: user\n",
    )
    write_memory(
        "coffee.md",
        "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
        "See [[Release owner (approved)]].\n",
    )
    write_index(
        "- [Release owner](release-owner.md) — who owns releases\n"
        "- [Coffee](coffee.md) — oat milk\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    section_ids = {
        attrs["id"]
        for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
    }
    outgoing_hrefs = [
        attrs["href"]
        for tag, attrs in elements
        if tag == "a" and attrs.get("href", "").startswith("#entry-")
    ]

    assert outgoing_hrefs, "expected the wikilink to resolve to a link"
    for href in outgoing_hrefs:
        assert href[1:] in section_ids, f"{href} does not match any entry id"
