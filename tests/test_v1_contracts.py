"""CLI characterization of raw headings and independent presentation orders."""

import re


def test_first_raw_heading_inside_a_fence_supplies_the_headline(
    run_cli, adopter_dir, write_unit, write_index
):
    """A later Markdown heading must not displace the first raw-text heading."""
    write_index("")
    write_unit(
        "kb-0001.md", "id: kb-0001\nevidence: measured\n",
        "```text\n# Inside <fence>\n```\n\n# Outside heading\n",
    )
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert '<span class="headline">Inside &lt;fence&gt;</span>' in page
    assert '<span class="headline">Outside heading</span>' not in page


def test_card_anchors_keep_declaration_order(
    run_cli, adopter_dir, write_unit, write_index
):
    """Reverse lexical systems separate declaration order from a sorted card."""
    write_index("")
    write_unit(
        "kb-0001.md", "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: zebra\n    kind: ref\n    captured_at: 2026-01-01\n"
        "    payload: {}\n"
        "  - system: alpha\n    kind: ref\n    captured_at: 2026-01-01\n"
        "    payload: {}\n",
    )
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    card = page.split('<ul class="anchors">', 1)[1]
    assert re.findall(r'<span class="system">([^<]+)</span>', card) == [
        "zebra", "alpha",
    ]


def test_unprobed_queue_breaks_equal_envelopes_by_canonical_payload(
    run_cli, adopter_dir, write_unit, write_index
):
    """Equal unit/system/kind force payload JSON, not declaration order, to decide."""
    write_index("")
    write_unit(
        "kb-0001.md", "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: ref\n    captured_at: 2026-01-01\n"
        "    payload:\n      ref: zebra\n"
        "  - system: repo\n    kind: ref\n    captured_at: 2026-01-01\n"
        "    payload:\n      ref: alpha\n",
    )
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    queue = page.split('<ul class="unprobed">', 1)[1].split("</ul>", 1)[0]
    assert re.findall(r'<pre class="payload">(.*?)</pre>', queue) == [
        '{"ref": "alpha"}', '{"ref": "zebra"}',
    ]


def test_map_group_orders_links_by_unit_id_not_filename_or_headline(
    run_cli, adopter_dir, write_unit, write_index
):
    """Creation, filename and headline orders all oppose the required id order."""
    write_index("")
    for filename, unit_id, headline in (
        ("a.md", "kb-0002", "Alpha"),
        ("z.md", "kb-0001", "Zebra"),
    ):
        write_unit(
            filename, f"id: {unit_id}\nevidence: measured\nanchors:\n"
            "  - system: repo\n    kind: ref\n    captured_at: 2026-01-01\n"
            "    payload: {}\n", f"# {headline}\n",
        )
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    group = page.split('<ul class="group-units">', 1)[1].split("</ul>", 1)[0]
    assert re.findall(r'<a href="#unit-([^"]+)">([^<]+)</a>', group) == [
        ("kb-0001", "Zebra"), ("kb-0002", "Alpha"),
    ]
