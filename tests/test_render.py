"""End-to-end tests for the `render` subcommand."""


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
