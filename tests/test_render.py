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


# The complete set of (element, attribute) pairs either view is allowed to
# emit anywhere on the page. This is a real whitelist, not a blacklist of
# attributes known to carry a URL: a blacklist misses a *relative* `href` on
# anything other than `<a>` (`<base href>`, `<link href>`, `<area href>`),
# and `<meta http-equiv="refresh" content="...">`, since `content` cannot be
# blacklisted -- the viewport meta uses it legitimately. Walking the parsed
# document against this set instead means any element or attribute neither
# view emits today fails the moment it appears, whatever it is called.
#
# `("a", "href")` is the only pair permitted to carry an external URL --
# checked separately, below, since membership here says nothing about a
# pair's *value*.
SELF_CONTAINED_ATTRIBUTES = {
    ("html", "lang"),
    ("meta", "charset"),
    ("meta", "name"),
    ("meta", "content"),
    ("section", "class"),
    ("section", "id"),
    ("section", "data-unit"),
    ("section", "data-state"),
    ("section", "data-name"),
    ("p", "class"),
    ("span", "class"),
    ("code", "class"),
    ("pre", "class"),
    ("ul", "class"),
    ("li", "class"),
    ("div", "class"),
    ("a", "href"),
    ("a", "target"),
    ("a", "rel"),
    ("svg", "class"),
    ("svg", "role"),
    # `html.parser.HTMLParser` lowercases attribute names, so the source's
    # `viewBox` is observed here as `viewbox`.
    ("svg", "viewbox"),
    ("svg", "width"),
    ("svg", "height"),
    ("svg", "aria-label"),
    ("rect", "x"),
    ("rect", "y"),
    ("rect", "width"),
    ("rect", "height"),
    ("rect", "fill"),
    ("text", "x"),
    ("text", "y"),
    ("text", "font-size"),
    ("line", "x1"),
    ("line", "y1"),
    ("line", "x2"),
    ("line", "y2"),
    ("line", "stroke"),
    ("line", "stroke-width"),
}


def _assert_self_contained(page, page_elements):
    """The self-containment scan both pages' tests share.

    A real whitelist over the parsed document, not a blacklist of
    substrings: every (element, attribute) pair anywhere on the page must be
    in `SELF_CONTAINED_ATTRIBUTES`, `("a", "href")` is the only pair allowed
    to carry an external URL, and no `<meta>` is an `http-equiv` refresh.
    Kept as one helper so `knowledge.html` and `memory.html` cannot drift
    apart on what "self-contained" means. Returns the parsed elements so a
    caller can run further checks over the same parse.
    """
    elements = page_elements(page)
    for tag, attrs in elements:
        if tag == "meta":
            assert "http-equiv" not in attrs, f"<meta http-equiv> found: {attrs}"
        for name, value in attrs.items():
            assert (tag, name) in SELF_CONTAINED_ATTRIBUTES, (
                f"{tag}[{name}]={value!r} is outside the self-containment whitelist"
            )
            if (tag, name) != ("a", "href"):
                assert "://" not in (value or ""), (
                    f"{tag}[{name}]={value!r} carries a URL outside the whitelist"
                )
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"
    return elements


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
    elements = _assert_self_contained(page, page_elements)

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


def test_the_memory_basis_line_names_the_path_like_the_knowledge_page_does(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # `knowledge.html`'s basis line names the path units were read under
    # ("... under knowledge/"); `memory.html`'s omitted it. The two pages
    # should agree on what "basis" discloses.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n")
    write_index("- [Coffee](coffee.md) — oat milk\n")

    run_cli("render", cwd=adopter_dir)
    knowledge_page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    memory_page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert "1 unit(s) under knowledge/" in knowledge_page
    assert "1 memory file(s) under memory/" in memory_page


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


def test_an_undeclared_name_falling_back_to_the_filename_does_not_collide_with_a_declared_name(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `alpha.md` declares no `name`, so its identity falls back to its
    # filename, "alpha". `other.md` declares `name: alpha` -- a real
    # declared name, just not `alpha.md`'s own (a `lint` ERROR the memory
    # view does not gate on, since it does not enforce). Before the fix both
    # entries anchor at id="entry-alpha": a `[[alpha]]` reference resolves
    # through `by_name` to `other.md`, but the href built from the same
    # collided id lands the reader on `alpha.md` instead.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("alpha.md", "description: no name here\nmetadata:\n  type: user\n")
    write_memory(
        "other.md",
        "name: alpha\ndescription: the real alpha\nmetadata:\n  type: user\n",
    )
    write_memory(
        "gamma.md",
        "name: gamma\ndescription: refers to alpha\nmetadata:\n  type: user\n",
        "See [[alpha]].\n",
    )
    write_index(
        "- [Alpha](alpha.md) — no name here\n"
        "- [Other](other.md) — the real alpha\n"
        "- [Gamma](gamma.md) — refers to alpha\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    section_ids = [
        attrs["id"] for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
    ]
    assert len(section_ids) == len(set(section_ids)), f"duplicate ids: {section_ids}"
    assert "entry-file-alpha" in section_ids
    assert "entry-alpha" in section_ids

    outgoing_hrefs = [
        attrs["href"] for tag, attrs in elements
        if tag == "a" and attrs.get("href", "").startswith("#entry")
    ]
    assert "#entry-alpha" in outgoing_hrefs
    assert "#entry-file-alpha" not in outgoing_hrefs


def test_an_unresolved_reference_is_not_linked_from_the_incoming_side_either(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `[[noname]]` targets a memory with no declared `name`, so `by_name`
    # resolution -- the only resolution the outgoing list tests against --
    # marks it unresolved, matching `lint`. The incoming list must agree: it
    # is the mirror image of the outgoing list, not a second, looser notion
    # of what counts as a reference. Before the fix, `noname.md`'s own
    # incoming list still lists `other.md` as a referrer (keyed by the raw
    # wikilink text, unfiltered by `resolution.by_name`) and links to it --
    # a live link for the very reference the outgoing side just marked
    # unresolved.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "noname.md", "description: has no declared name\nmetadata:\n  type: user\n"
    )
    write_memory(
        "other.md",
        "name: other\ndescription: refers to noname\nmetadata:\n  type: user\n",
        "See [[noname]].\n",
    )
    write_index(
        "- [Noname](noname.md) — has no declared name\n"
        "- [Other](other.md) — refers to noname\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert 'class="unresolved"' in page
    # The only entry that could ever link to "other.md" is noname.md's own
    # incoming list -- nothing else on this page names "other". A live link
    # there would be the incoming side treating a reference the outgoing
    # side marked unresolved as if it had resolved.
    assert not [
        attrs for tag, attrs in elements
        if tag == "a" and attrs.get("href") == "#entry-other"
    ]


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

    elements = _assert_self_contained(page, page_elements)
    assert not [tag for tag, _ in elements if tag == "script"]


def test_a_null_recorded_at_reads_as_absent_in_the_list_and_the_strip_alike(
    run_cli, adopter_dir, write_unit, page_elements
):
    # `recorded_at` is not a key field and nothing validates it, so an
    # explicit `null` is a legal record. `html.escape_text` (the history
    # list) and `html.escape_attribute` (the strip's `aria-label`) must spell
    # an absent value the same way -- not "" in one place and the literal
    # word "None" in the other.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": None},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert "None" not in page
    strip = next(
        attrs for tag, attrs in elements
        if tag == "svg" and attrs.get("class") == "freshness"
    )
    assert "None" not in strip["aria-label"]


def test_the_freshness_strip_is_drawn_and_ends_at_the_last_record(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted", "recorded_at": "2026-02-01T00:00:00Z"},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert any(tag == "svg" for tag, _ in page_elements(page))
    assert "2026-02-01T00:00:00Z" in page
    assert "drifted" in page


def test_no_confluence_is_drawn_for_a_two_link_chain(
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

    assert not [attrs for tag, attrs in page_elements(page)
                if tag == "svg" and attrs.get("class") == "confluence"]


def test_a_confluence_is_drawn_when_three_units_are_superseded_at_once(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    for old in ("kb-0001", "kb-0002", "kb-0003"):
        write_unit(f"{old}.md", f"id: {old}\nevidence: hypothesis\n", f"# {old}\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\n",
        "# The one that replaced them\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert [attrs for tag, attrs in page_elements(page)
            if tag == "svg" and attrs.get("class") == "confluence"]


SVG_FORBIDDEN_ELEMENTS = {"use", "image", "iframe", "object", "embed", "script"}


def test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup(
    run_cli, adopter_dir, write_unit, page_elements
):
    # A page where BOTH diagrams are actually drawn: a confluence (three
    # units superseded at once) and a freshness strip (several probes of one
    # anchor). The generic URL-whitelist and hostile-content tests elsewhere
    # in this file exercise pages with no anchors and no three-way
    # supersession, so they never contain an <svg> at all -- they would pass
    # unchanged even if `svg.py` started emitting a `<use href=...>`. This
    # test exists so that scan actually has an SVG to scan.
    run_cli("init", cwd=adopter_dir)
    for old in ("kb-0001", "kb-0002", "kb-0003"):
        write_unit(f"{old}.md", f"id: {old}\nevidence: hypothesis\n", f"# {old}\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# The one that replaced them\n",
    )
    # A hostile `recorded_at` -- angle brackets and a quote -- on the LAST
    # record: this is the one value both the strip's per-band <title> and
    # its right-edge aria-label read, so it is the sharpest place a missed
    # escape would show up as live markup.
    _log(adopter_dir, [
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted",
         "recorded_at": '2026-02-01T00:00:00Z"><script>alert(1)</script>'},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    # The page really does draw both diagrams -- asserted by count, not just
    # "any svg", so this test cannot go vacuous the way the reused one did.
    svgs = [(tag, attrs) for tag, attrs in elements if tag == "svg"]
    assert len(svgs) == 2, svgs
    assert {attrs.get("class") for _, attrs in svgs} == {"freshness", "confluence"}

    # The hostile `recorded_at` reaches the page escaped, inside the strip's
    # <title>, never as an unescaped tag.
    assert '"><script>alert(1)</script>' not in page
    assert (
        '2026-02-01T00:00:00Z"&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in page
    )

    for tag, attrs in elements:
        assert tag not in SVG_FORBIDDEN_ELEMENTS, f"<{tag}> must never appear"
        for name, value in attrs.items():
            assert not name.lower().startswith("on"), f"{tag}[{name}] is an event attribute"

    _assert_self_contained(page, page_elements)


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


def test_only_existing_regenerates_what_is_there_and_creates_nothing(
    run_cli, adopter_dir, write_unit
):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") != "stale\n"
    assert not (adopter_dir / "memory.html").exists()


def test_an_existing_artifact_that_is_not_valid_utf8_is_overwritten_not_raised(
    run_cli, adopter_dir, write_unit
):
    # `write_if_changed` reads the existing artifact only to decide whether a
    # write is needed. A file it cannot decode is not thereby known to equal
    # what is about to be written, so the safe default is to write over it --
    # never a traceback on a page a reader has no repository to debug.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_bytes(b"\xff\xfe not valid utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "render: wrote knowledge.html" in result.stdout
    assert (
        (adopter_dir / "knowledge.html")
        .read_text(encoding="utf-8")
        .startswith("<!doctype html>")
    )


def test_only_existing_is_fail_open_on_an_unwritable_working_directory(
    run_cli, adopter_dir, write_unit
):
    # The other reproduction: the temporary file itself cannot be written
    # (here, a read-only working directory). Fail-open is documented for
    # `--only-existing`, so this must warn and exit 0, leaving the artifact
    # already on disk exactly as it was -- not a `PermissionError` traceback
    # on every session start.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    adopter_dir.chmod(0o555)
    try:
        result = run_cli("render", "--only-existing", cwd=adopter_dir)
    finally:
        adopter_dir.chmod(0o755)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_a_write_failure_gates_when_render_runs_explicitly(
    run_cli, adopter_dir, write_unit
):
    # The mirror case: run explicitly (no `--only-existing`), the same write
    # failure gates as an ERROR does, same as any other finding -- a person
    # asking for the views by hand is entitled to be told they were not
    # written, without a traceback.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    adopter_dir.chmod(0o555)
    try:
        result = run_cli("render", cwd=adopter_dir)
    finally:
        adopter_dir.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ERROR" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_is_fail_open_on_an_invalid_corpus(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    unattended = run_cli("render", "--only-existing", cwd=adopter_dir)
    explicit = run_cli("render", cwd=adopter_dir)

    assert unattended.returncode == 0
    assert "WARNING" in unattended.stderr
    # Fail-open does NOT mean "publish a page built on rejected data": the
    # artifact already on disk is left exactly as it was.
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"
    assert explicit.returncode == 1


def test_only_existing_with_neither_artifact_present_is_a_clean_no_op(
    run_cli, adopter_dir, write_unit
):
    # Nobody has activated either view yet: `--only-existing` must not
    # create one, and -- since there is nothing to regenerate -- it must
    # not even read or validate the corpus to get there. An adopter who
    # never ran `init --view` sees no output and no findings from the
    # startup hook.
    _scaffold(run_cli, adopter_dir, write_unit)

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert result.stderr == ""
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_only_existing_is_fail_open_on_a_corrupt_verdict_log(
    run_cli, adopter_dir, write_unit
):
    # The broader ruling: unattended mode downgrades every build failure,
    # not only the contract's own ERROR findings. A verdict log this reader
    # cannot parse is one such failure -- same fail-open contract, same
    # "leave the artifact exactly as it was" guarantee.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    (adopter_dir / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_is_fail_open_on_a_missing_memory_index(
    run_cli, adopter_dir, write_unit
):
    # Same ruling, over the memory-layer read precondition rather than the
    # curated-layer contract or the verdict log.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    (adopter_dir / "memory" / "MEMORY.md").unlink()

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_regenerates_memory_when_only_memory_exists(
    run_cli, adopter_dir, write_unit
):
    # The mirror of the brief's own case: with `knowledge.html` absent and
    # `memory.html` present, only the latter is regenerated and the former
    # is still not created.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory.html").write_text("stale\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert (adopter_dir / "memory.html").read_text(encoding="utf-8") != "stale\n"
    assert not (adopter_dir / "knowledge.html").exists()
