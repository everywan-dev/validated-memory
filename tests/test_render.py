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
