"""CLI regressions for capture-analysis consolidation."""

from test_incorporation import ATTR, SCOPE, Workspace, proposal_args


def adopter_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file()
    }


def candidate(path, *, invalid=False):
    extra = 'unknown: value\n' if invalid else ''
    path.write_text(
        f'---\nid: new\nevidence: verifiable\n{extra}---\nProspective finding.\n'
    )
    return path


def test_invalid_canonical_input_in_non_target_project_still_refuses_atomically(tmp_path, run_cli):
    workspace = Workspace(tmp_path, run_cli)
    other = tmp_path / 'other-project'
    other.mkdir()
    assert run_cli('init', cwd=other).returncode == 0
    workspace.ok('register', 'other', str(other), '--source', 'other')
    (other / 'knowledge/invalid.md').write_text(
        '---\nid: invalid\nevidence: verifiable\nunknown: value\n---\nInvalid canonical input.\n'
    )
    proposed = candidate(tmp_path / 'candidate.md')
    before = workspace.store.read_bytes(), adopter_bytes(workspace.project), adopter_bytes(other)

    result = workspace.call(*proposal_args(workspace, proposed))

    assert result.returncode == 1 and result.stdout == ''
    assert 'canonical validation failed' in result.stderr and 'unknown' in result.stderr
    assert (workspace.store.read_bytes(), adopter_bytes(workspace.project), adopter_bytes(other)) == before


def test_invalid_prospective_input_still_refuses_atomically(tmp_path, run_cli):
    workspace = Workspace(tmp_path, run_cli)
    proposed = candidate(tmp_path / 'candidate.md', invalid=True)
    before = workspace.store.read_bytes(), adopter_bytes(workspace.project)

    result = workspace.call(*proposal_args(workspace, proposed))

    assert result.returncode == 1 and result.stdout == ''
    assert 'canonical validation failed' in result.stderr and 'unknown' in result.stderr
    assert (workspace.store.read_bytes(), adopter_bytes(workspace.project)) == before
