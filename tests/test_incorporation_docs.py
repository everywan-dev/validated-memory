"""Shipped lifecycle guidance, executable examples and compatibility boundaries."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'docs/reference/incorporation.md'
STORAGE = ROOT / 'docs/reference/incorporation-storage.md'
SKILLS = [ROOT / 'skills' / name / 'SKILL.md' for name in
          ('create-knowledge-unit', 'supersede-knowledge', 'consult-project-memory')]


@pytest.mark.parametrize('path', [REFERENCE, STORAGE, *SKILLS])
def test_lifecycle_guidance_is_self_contained_and_links_resolve(path):
    text = path.read_text(encoding='utf-8')
    assert 'sessions/' not in text
    for target in re.findall(r'\]\(([^)]+)\)', text):
        if '://' in target or target.startswith('#'):
            continue
        assert (path.parent / target.split('#', 1)[0]).is_file(), target


def test_public_entry_points_disclose_unreleased_lifecycle_and_upgrade():
    for relative in ('README.md', 'docs/reference/cli.md', 'docs/reference/consultation.md',
                     'docs/reference/incorporation.md', 'docs/reference/incorporation-storage.md'):
        text = (ROOT / relative).read_text(encoding='utf-8').lower()
        assert 'unreleased' in text
        assert '2.2.0' in text and 'schema 1' in text
        assert 'upgrade' in text
    for path in SKILLS:
        text = path.read_text(encoding='utf-8')
        assert 'incorporation.md' in text and 'unreleased' in text


def test_public_workflow_has_complete_shell_blocks():
    blocks = re.findall(r'```bash\n(.*?)\n```', REFERENCE.read_text(), re.S)
    assert len(blocks) == 4
    for block in blocks:
        result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    result = subprocess.run(['bash', '-n'], input='\n'.join(blocks), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_handle_helper_distinguishes_committed_inspection_and_receipt():
    text = REFERENCE.read_text()
    for required in ('invoke "$@" || return', 'assert len(rows) == 2',
                     'rows[0]["status"] == "inspected" and "id" not in rows[0]',
                     'rows[1]["status"] == "receipt recorded"',
                     'rows[0]["status"] == "historical material; not a current evidence check"',
                     'rows[1]["status"] == "inspection recorded"',
                     'print(rows[-1]["id"])', '.args.json', '.stderr', '.exit'):
        assert required in text


def test_lifecycle_command_table_and_observation_states_are_complete():
    text = REFERENCE.read_text()
    for operation in ('submit', 'challenge', 'inspect', 'decide', 'renew', 'incorporate',
                      'resolve', 'reconcile', 'address', 'track-publication', 'reflect'):
        assert f'| `{operation} ' in text or f'| `{operation}`' in text
    for status in ('current', 'stale snapshot', 'review required', 'conflict/invalid binding',
                   'unavailable', 'not-open', 'open', 'resolved'):
        assert f'`{status}`' in text


def test_storage_exposes_closed_shapes_and_legacy_preservation():
    text = STORAGE.read_text()
    for required in ('Every object below is closed', 'CREATE TABLE workspace',
                     'CREATE TABLE events', 'review_frontier', 'installed_binding',
                     'observed_head', 'consumer_proof', 'dependency-removed',
                     'schema_version = 2', 'BEGIN IMMEDIATE', 'payload TEXT',
                     'No change to Snapshot', 'version:2', 'support-review'):
        assert required in text
    assert 'Q1 below' not in text and 'Architect decisions' not in text


def test_workflow_keeps_fresh_review_and_explicit_effect_boundaries():
    text = ' '.join(REFERENCE.read_text().split())
    for required in ('binding inspection follows acceptance',
                     'No fake support revision', 'negative control',
                     'newly inserted use', 'Omitting reference flags means none',
                     'Default no-flags renewal must refuse changed support',
                     'before rewriting', 'does not undo authoring',
                     'does not establish usefulness'):
        assert required.lower() in text.lower()
