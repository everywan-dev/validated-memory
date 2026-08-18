"""End-to-end tests for the `render` subcommand."""

import re


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
