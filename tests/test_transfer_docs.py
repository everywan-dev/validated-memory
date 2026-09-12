"""Public transfer guidance, disclosure and executable shell structure."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'docs/reference/transfer.md'
STORAGE = ROOT / 'docs/reference/transfer-storage.md'
SKILLS = [ROOT / 'skills' / name / 'SKILL.md' for name in
          ('consult-project-memory', 'create-knowledge-unit', 'supersede-knowledge')]
ENTRY_POINTS = [ROOT / path for path in
                ('README.md', 'docs/reference/cli.md', 'docs/reference/consultation.md',
                 'docs/reference/consultation-storage.md',
                 'docs/reference/incorporation.md', 'docs/reference/incorporation-storage.md')]


@pytest.mark.parametrize('path', [REFERENCE, STORAGE, *SKILLS, *ENTRY_POINTS])
def test_transfer_guidance_is_public_and_linked(path):
    text = path.read_text()
    assert 'sessions/' not in text
    assert '/home/' not in text
    for target in re.findall(r'\]\(([^)]+)\)', text):
        if '://' in target or target.startswith('#'):
            continue
        assert (path.parent / target.split('#', 1)[0]).exists(), (path, target)


@pytest.mark.parametrize('path', [REFERENCE, STORAGE, *ENTRY_POINTS])
def test_transfer_entry_points_distinguish_released_schema3_from_legacy(path):
    text = path.read_text().lower()
    assert '2.3.0' in text and '2.2.0' in text and 'schema 1' in text
    assert 'schema 3' in text and 'upgrade' in text


def test_transfer_public_shell_blocks_parse_as_one_workflow():
    blocks = re.findall(r'```bash\n(.*?)\n```', REFERENCE.read_text(), re.S)
    assert len(blocks) == 3
    result = subprocess.run(['bash', '-n'], input='\n'.join(blocks), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_transfer_workflow_records_wire_bytes_and_adopter_preservation():
    text = REFERENCE.read_text()
    for required in ('adopter_state()', 'adopter_unchanged=before == after',
                     'stdout_bytes=len(result.stdout)', 'stderr_bytes=len(result.stderr)',
                     'assert result.returncode == int(expected)',
                     'assert len(rows) == 2', "'link-transfer': ('inspected', 'recorded')",
                     "'read': ('inspected', 'receipt recorded')", "print(rows[-1]['id'])"):
        assert required in text


def test_transfer_workflow_keeps_origin_history_and_review_stages_explicit():
    text = REFERENCE.read_text()
    for required in ('--include-workspace-history --assess', 'no A1 receipt',
                     '--origin-only-predecessor', '--dependency', 'link-transfer',
                     'basis_incorporation', 'plan_inspection', 'plan_decision', 'plan_incorporation',
                     'record-use destination:kb-local-plan', 'show-transfer',
                     'invoke b 1 check-use', 'assert len(encoded) + 1 <= 1048576',
                     'does not establish empirical usefulness'):
        assert required in text
    assert text.index('retain basis_incorporation') < text.index('retain plan_proposal')
    assert text.index('retain plan_link') < text.index('retain plan_inspection') < text.index('retain plan_decision')


def test_transfer_storage_documents_exact_envelopes_and_replay_boundaries():
    text = ' '.join(STORAGE.read_text().split())
    for required in ('schema_version = 3', 'transfer-import', 'transfer-link', 'source_store_path',
                     'all six fields', 'one final newline', '32 MiB', '8 MiB', '16 distinct',
                     '10,000', '128', '512', 'lineage', 'frontier', 'predecessor_files',
                     'origin-only', 'independent', 'observed', 'live_origin',
                     'receipt1/2', 'schema_version: 1'):
        assert required.lower() in text.lower()


@pytest.mark.parametrize('path', SKILLS)
def test_transfer_skills_preserve_explicit_review_and_no_implicit_authorization(path):
    text = path.read_text()
    assert 'transfer.md' in text and '2.3.0' in text
    assert 'schema 3' in text and '2.2.0' in text
    assert 'origin' in text and 'successor' in text


def test_transfer_delivery_guidance_distinguishes_commit_from_output():
    for path in (REFERENCE, STORAGE):
        text = ' '.join(path.read_text().lower().split())
        assert 'precommit failure rolls back' in text
        assert 'postcommit delivery failure may retain the link' in text
        assert 'retry validates' in text
    assert '--include-workspace-history > /dev/null' in REFERENCE.read_text()


def test_transfer_inspection_documents_qualified_transitive_material():
    text = ' '.join(STORAGE.read_text().split())
    for required in ('{workspace,identity,file}', '{workspace,project,path,sha256,size,text}',
                     '{workspace,event}', 'transitive corrections',
                     'intermediary transfer-link events', 'conflicting revisions never silently overwrite',
                     "selected root's direct source supersedes"):
        assert required in text


def test_transfer_assessment_and_inventory_cover_transitive_limits_and_links():
    text = ' '.join(STORAGE.read_text().split())
    for required in ('supported refusals history_events', 'including its outer workspace',
                     'event-work limits', 'unknown destination',
                     'direct and transitive relationships', 'Independent and retired historical links'):
        assert required in text


def test_transfer_legacy_ancestry_guidance_preserves_history_and_explains_recovery():
    for path in (REFERENCE, STORAGE):
        text = ' '.join(path.read_text().split())
        assert 'receipt1/2' in text
        assert 'reconsult' in text and 'receipt3 lineage' in text
    text = ' '.join(STORAGE.read_text().split())
    assert 'that chronological prefix uses its frozen original checks' in text
    assert 'refuse atomically' in text


def test_transfer_budget_and_scope_guidance_cover_the_complete_judgment():
    text = ' '.join(STORAGE.read_text().split())
    for required in ('helpers never reset the budget', 'Each inventory row is an independent rooted judgment',
                     'histories are not unioned into one 128-node cap',
                     'Link artifacts count as edges', 'requested read scope, including a narrower scope',
                     'Local acceptance does not bypass an applicable origin gate'):
        assert required in text


def test_transfer_declared_dependency_proof_guidance_preserves_unbound_references():
    text = ' '.join(STORAGE.read_text().split())
    for required in ('both resolution remedies, create, retry and replay',
                     'declared dependencies transitively and canonical ancestry',
                     'exception covers only the exact target',
                     'Unbound referenced units remain permitted', 'no new payload field',
                     'retain a reviewed binding, or supported retained canonical lineage, then retry',
                     'Frozen histories before transfer links retain their original behavior'):
        assert required in text
