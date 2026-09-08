"""Public CLI and structural contracts for enhanced knowledge exports."""

import json
import re

import pytest


def test_identical_strips_have_unique_stable_description_references(
    run_cli, adopter_dir, write_unit, page_elements
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    records = []
    for number in range(1, 3):
        unit = f"kb-000{number}"
        write_unit(f"{unit}.md", f"id: {unit}\nevidence: measured\nanchors:\n"
                   "  - system: repo\n    kind: git_ref\n"
                   "    captured_at: 2026-01-01\n    payload: {}\n")
        records.append(dict(unit=unit, system="repo", kind="git_ref", payload={},
                            verdict="current"))
    (adopter_dir / "verdicts.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records))
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text()
    elements = page_elements(page)
    descriptions = [a["id"] for tag, a in elements if tag == "desc"]
    references = [a.get("aria-describedby") for tag, a in elements if tag == "svg"]
    assert len(references) == 2
    assert None not in references
    assert len(set(references)) == 2
    assert references == descriptions
    assert page.count('class="record"') == 2
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert (adopter_dir / "knowledge.html").read_text() == page


@pytest.mark.parametrize("count,length,numbered", [(8, 48, False), (8, 49, True),
                                                   (9, 48, True), (9, 49, True)])
def test_rationale_numbering_boundaries_keep_full_alternatives(
    run_cli, adopter_dir, write_unit, count, length, numbered
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    labels = [str(i) + "x" * (length - 1) for i in range(count)]
    options = "".join(f'    - label: "{label}"\n'
                      f'      disposition: {"chosen" if i == 0 else "rejected"}\n'
                      f'      reason: "Reason {i}"\n' for i, label in enumerate(labels))
    write_unit("kb-0001.md", 'id: kb-0001\nevidence: measured\nrationale:\n'
               '  question: "Which option?"\n  options:\n' + options)
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text()
    diagram = re.search(r'<svg class="rationale".*?</svg>', page, re.S).group()
    assert (">#1</text>" in diagram) is numbered
    assert "more than 8 options" in diagram
    assert "48 characters" in diagram
    for label in labels:
        assert f'class="label" dir="auto">{label}</span>' in page


def test_app_is_exact_canonical_page_plus_one_trusted_script(
    run_cli, adopter_dir, write_unit, page_elements
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    hostile = "</script><img src=x onerror=alert(1)>"
    write_unit("kb-0001.md", 'id: kb-0001\nevidence: hypothesis\nrationale:\n'
               f'  question: "{hostile}"\n  options:\n'
               f'    - label: "{hostile}"\n      disposition: chosen\n'
               f'      reason: "{hostile}"\n'
               '    - label: "Other"\n      disposition: rejected\n'
               '      reason: "No"\n', hostile)
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge-app.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1
    assert re.sub(r"<script>.*?</script>", "", page, flags=re.S) == (
        adopter_dir / "knowledge.html").read_text()
    assert [attrs for tag, attrs in page_elements(page) if tag == "script"] == [{}]
    assert hostile not in scripts[0]
    # Source lint only: browser review separately checks runtime behavior/CSP.
    assert not re.search(r"innerHTML|eval\s*\(|Function\s*\(|fetch\s*\(|"
                         r"XMLHttpRequest|WebSocket|import\s*\(|localStorage|"
                         r"sessionStorage", scripts[0])
    first = (adopter_dir / "knowledge-app.html").read_bytes()
    stamp = (adopter_dir / "knowledge-app.html").stat().st_mtime_ns
    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert (adopter_dir / "knowledge-app.html").read_bytes() == first
    assert (adopter_dir / "knowledge-app.html").stat().st_mtime_ns == stamp
