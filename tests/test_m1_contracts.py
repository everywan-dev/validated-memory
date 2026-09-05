"""CLI pins for payload identity, display formatting and append-order history."""


def test_nested_mapping_order_does_not_change_anchor_identity(
    run_cli, adopter_dir, write_unit
):
    """Reordered nested keys must match the log, removing the unprobed entry."""
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01\n    payload:\n"
        "      z: last\n      nested:\n        z: final\n        a: first\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        '{"unit":"kb-0001","system":"repo","kind":"git_ref",'
        '"payload":{"nested":{"a":"first","z":"final"},"z":"last"},'
        '"verdict":"current","recorded_at":"2026-01-02"}\n',
        encoding="utf-8",
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert '<ul class="unprobed">' not in page
    assert '<span class="verdict">current</span>' in page
    assert "1 record(s) for this anchor; showing 1." in page


def test_list_order_distinguishes_anchors_and_json_format_matches_queue_and_card(
    run_cli, adopter_dir, write_unit
):
    """Lists retain identity order; both payload displays use escaped default JSON."""
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01\n    payload:\n"
        "      z: café\n      refs:\n        - next\n        - main\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        '{"unit":"kb-0001","system":"repo","kind":"git_ref",'
        '"payload":{"refs":["main","next"],"z":"café"},'
        '"verdict":"current","recorded_at":"2026-01-02"}\n',
        encoding="utf-8",
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    payload = '<pre class="payload">{"refs": ["next", "main"], "z": "caf\\u00e9"}</pre>'
    queue = page.split('<ul class="unprobed">')[1].split("</ul>")[0]
    card = page.split('<ul class="anchors">')[1]
    assert payload in queue
    assert payload in card
    assert '<span class="verdict">unknown</span>' in page
    assert "0 record(s) for this anchor; showing 0." in card


def test_history_and_latest_follow_file_order_with_legacy_and_orphan_totals(
    run_cli, adopter_dir, write_unit
):
    """Reverse timestamps distinguish append order; unmatched records stay in totals."""
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01\n    payload: {}\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        '{"unit":"kb-0001","system":"repo","kind":"git_ref",'
        '"payload":{},"verdict":"drifted","recorded_at":"2026-09-03"}\n'
        '{"unit":"kb-0001","system":"repo","kind":"git_ref",'
        '"payload":{},"verdict":"current","recorded_at":"2026-09-01"}\n'
        '{"unit":"kb-0001","system":"repo","kind":"git_ref",'
        '"verdict":"drifted","recorded_at":"2026-09-04"}\n'
        '{"unit":"kb-orphan","system":"repo","kind":"git_ref",'
        '"payload":{},"verdict":"unknown","recorded_at":"2026-09-05"}\n',
        encoding="utf-8",
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert '<span class="verdict">current</span>' in page
    assert "Verdict log: 4 record(s)" in page
    assert "of which 2 belong to an anchor shown below" in page
    assert "2 record(s) for this anchor; showing 2." in page
    history = page.split('<ul class="history">')[1].split("</ul>")[0]
    assert history.count('<li class="record">') == 2
    assert history.index("2026-09-01 current") < history.index("2026-09-03 drifted")
    assert "2026-09-04" not in history
    assert "2026-09-05" not in history
