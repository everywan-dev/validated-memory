"""Structural export guards; actual layout is verified separately in Chrome."""

import re


def test_narrow_overview_keeps_all_columns_and_wraps_exported_table(
    adopter_dir, run_cli, write_unit
):
    """Pin the responsive CSS shipped through the CLI, not browser geometry."""
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    write_unit("kb-1.md", "id: kb-1\nevidence: measured\n")
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    canonical = (adopter_dir / "knowledge.html").read_text()
    app = (adopter_dir / "knowledge-app.html").read_text()
    assert re.sub(r"<script>.*?</script>", "", app, flags=re.S) == canonical
    narrow = canonical.split("@media (max-width: 35rem) {", 1)[1].split(
        "@media", 1
    )[0]
    assert re.search(r"table\.counts\s*\{[^}]*width:\s*100%", narrow)
    assert "table-layout: fixed" in narrow
    assert re.search(r"table\.counts th, table\.counts td\s*\{[^}]*"
                     r"padding:\s*\.25rem;[^}]*overflow-wrap:\s*anywhere", narrow)
    assert "table.counts thead th:first-child { width: 30%; }" in narrow
    assert "font-size" not in narrow
    for label in ("evidence", "current", "drifted", "unknown", "total"):
        assert f'>{label}</th>' in canonical


def test_metadata_contrast_rules_keep_dark_screen_override_out_of_print(
    adopter_dir, run_cli
):
    """Pin emitted scheme rules; Chrome checks computed colors separately."""
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text()
    assert ".meta { color: #666;" in page
    assert re.search(r"@media screen and \(prefers-color-scheme: dark\)\s*\{"
                     r"\s*\.meta\s*\{\s*color:\s*#aaa;\s*\}", page)
    memory = (adopter_dir / "memory.html").read_text()
    assert ".meta { color: rgba(127,127,127,1);" in memory
    assert "prefers-color-scheme" not in memory
