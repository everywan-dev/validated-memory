"""Complete synthetic everyday correction lifecycle through CLI subprocesses."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
SCOPE = ('--scope', 'exercise=dispatch')
ATTR = ('--actor', 'synthetic-reviewer', '--reason', 'Predetermined fixture judgment.')
OLD = 'Dispatch note: carrier departure is at 16:00.\n'
NEW = 'Reviewed testimony: 16:00 is admission cutoff; departure is scheduled later.\n'


def canonical(identity, text, predecessor=None):
    fields = f'id: {identity}\nevidence: verifiable\n'
    if predecessor:
        fields += f'supersedes:\n  - {predecessor}\n'
    return f'---\n{fields}---\n{text}'


class EverydayCase:
    def __init__(self, base):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)
        self.transcript = base / 'transcript'
        self.transcript.mkdir()
        self.phase = 'setup'
        self.records = []
        self.initializations = []
        self.handles = {}
        self.roots = {name: base / name for name in ('a', 'b', 'c')}
        self.env = {**os.environ, 'PYTHONPATH': str(REPO)}

    def initialize(self, name):
        root = self.roots[name]
        root.mkdir()
        command = [sys.executable, '-P', '-m', 'validated_memory', 'init']
        before = self.snapshot()
        started = time.perf_counter()
        result = subprocess.run(command, cwd=root, env=self.env, capture_output=True)
        record = dict(command=command, cwd=str(root), exit=result.returncode,
                      elapsed_seconds=time.perf_counter() - started,
                      stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr),
                      before=before, after=self.snapshot())
        stem = self.transcript / f'init-{name}'
        stem.with_suffix('.stdout').write_bytes(result.stdout)
        stem.with_suffix('.stderr').write_bytes(result.stderr)
        stem.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
        self.initializations.append(record)
        assert result.returncode == 0, result.stderr

    def snapshot(self):
        result = {}
        for name, root in self.roots.items():
            if root.exists():
                result[name] = {'.': 'directory'}
                for path in sorted(root.rglob('*')):
                    result[name][path.relative_to(root).as_posix()] = (
                        'directory' if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest())
        return result

    def call(self, workspace, *args, expected=0):
        before = self.snapshot()
        store = self.base / f'{workspace}.sqlite'
        old_store = store.read_bytes() if store.exists() else None
        command = [sys.executable, '-P', '-m', 'validated_memory', 'consultation',
                   '--store', str(store), *map(str, args)]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=self.base, env=self.env, capture_output=True)
        elapsed = time.perf_counter() - started
        after = self.snapshot()
        index = len(self.records) + 1
        stem = self.transcript / f'{index:03d}'
        stem.with_suffix('.stdout').write_bytes(result.stdout)
        stem.with_suffix('.stderr').write_bytes(result.stderr)
        record = dict(index=index, phase=self.phase, workspace=workspace, operation=args[0],
                      command=command, expected=expected, exit=result.returncode,
                      elapsed_seconds=elapsed, stdout_bytes=len(result.stdout),
                      stderr_bytes=len(result.stderr), before=before, after=after,
                      adopter_unchanged=before == after)
        self.records.append(record)
        stem.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
        assert before == after, (index, args, 'adopter mutation')
        assert result.returncode == expected, (index, args, result.stdout, result.stderr)
        assert b'Traceback' not in result.stderr
        if expected:
            assert store.read_bytes() == old_store, (index, 'refusal changed store')
            return result
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        if args[0] != 'export-transfer':
            assert all(row['schema_version'] == 1 and row['operation'] == args[0] for row in rows)
            assert len(rows) == (2 if args[0] in ('read', 'inspect', 'link-transfer') else 1)
        if args[0] == 'read':
            assert [r['status'] for r in rows] == ['inspected', 'receipt recorded']
        elif args[0] == 'link-transfer':
            assert [r['status'] for r in rows] == ['inspected', 'recorded']
        return rows

    def artifact(self, key, workspace, *args):
        rows = self.call(workspace, *args)
        handle = rows[-1]['id']
        assert len(handle) == 64
        self.handles[key] = {'workspace': workspace, 'id': handle}
        return handle

    def author(self, workspace, relative, text):
        path = self.roots[workspace] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def bind(self, key, workspace, identity, support, reference=None):
        args = ['bind', f'{workspace}:{identity}', '--support', support,
                '--authority', workspace, *SCOPE, *ATTR]
        if reference:
            args += ['--reference', f'{workspace}:{reference}']
        return self.artifact(key, workspace, *args)

    def accept(self, key, workspace, artifact):
        inspection = self.artifact(key + '_inspection', workspace, 'inspect', artifact)
        return self.artifact(key + '_decision', workspace, 'decide', artifact,
                             '--inspection', inspection, '--outcome', 'accept', *ATTR)

    def propose(self, key, workspace, identity, text, support, predecessor=None):
        candidate = self.base / f'{key}-candidate.md'
        candidate.write_text(canonical(identity, text, predecessor), encoding='utf-8')
        proposal = self.artifact(key, workspace, 'submit', workspace, candidate,
                                '--path', f'knowledge/{identity}.md', '--support', support,
                                '--authority', workspace, *SCOPE, *ATTR)
        return proposal, candidate

    def install(self, key, workspace, proposal, candidate, identity, support):
        decision = self.accept(key, workspace, proposal)
        self.author(workspace, f'knowledge/{identity}.md', candidate.read_text())
        binding = self.bind(key + '_binding', workspace, identity, support)
        incorporation = self.artifact(key + '_incorporation', workspace, 'incorporate',
                                      proposal, '--decision', decision, *ATTR)
        return binding, incorporation

    def challenge(self, key, workspace, identity):
        statement = self.base / f'{key}.txt'
        statement.write_text(NEW, encoding='utf-8')
        challenge = self.artifact(key, workspace, 'challenge', f'{workspace}:{identity}',
                                 '--statement', statement, '--kind', 'factual', *SCOPE, *ATTR)
        return challenge, self.accept(key, workspace, challenge)

    def resolve(self, key, workspace, challenge, decision, binding, incorporation):
        inspection = self.artifact(key + '_inspection', workspace, 'inspect', binding)
        return self.artifact(key, workspace, 'resolve', challenge, '--decision', decision,
                             '--incorporation', incorporation, '--inspection', inspection, *ATTR)

    def export(self, key, workspace, receipt):
        rows = self.call(workspace, 'export-transfer', receipt, '--include-workspace-history')
        path = self.base / f'{key}.json'
        path.write_bytes((self.transcript / f'{len(self.records):03d}.stdout').read_bytes())
        return path, rows[0]

    def use(self, key, workspace, identity):
        receipt = self.artifact(key + '_receipt', workspace, 'read', f'{workspace}:{identity}', *SCOPE)
        use = self.artifact(key, workspace, 'record-use', f'{workspace}:{identity}', '--receipt', receipt)
        return receipt, use


def run_case(base):
    case = EverydayCase(base)
    projects = {}
    for name in ('a', 'b', 'c'):
        root = case.roots[name]
        case.initialize(name)
        case.author(name, 'sources/original.txt', OLD)
        registration = case.artifact(name + '_registration', name, 'register', name, root, '--source', name)
        projects[name] = case.call(name, 'show', registration)[0]['artifact']['payload']['project']
    case.author('a', 'knowledge/departure.md', canonical('departure', OLD))
    case.bind('source_original_binding', 'a', 'departure', 'sources/original.txt')

    case.phase = 'first_transfer'
    source_receipt = case.artifact('source_original_receipt', 'a', 'read', 'a:departure', *SCOPE)
    capsule, first = case.export('original-transfer', 'a', source_receipt)
    imported = case.artifact('original_import', 'b', 'import-transfer', capsule, *ATTR)
    case.call('b', 'show-transfer', imported)
    proposal, candidate = case.propose('local_original_proposal', 'b', 'local-departure', OLD,
                                       'sources/original.txt')
    case.artifact('original_link', 'b', 'link-transfer', proposal, '--import', imported,
                  '--origin', f'{projects["a"]}:departure', *ATTR)
    case.install('local_original', 'b', proposal, candidate, 'local-departure', 'sources/original.txt')
    old_plan = 'Plan: all dispatch preparation must precede the assumed 16:00 departure.\n'
    case.author('b', 'sources/planning.txt', 'Synthetic planning requirement: distinguish cutoff from departure.\n')
    case.author('b', 'knowledge/plan.md', canonical('plan', old_plan))
    case.bind('old_plan_binding', 'b', 'plan', 'sources/planning.txt', 'local-departure')
    _, old_use = case.use('old_use', 'b', 'plan')
    case.author('b', 'exports/report.txt', old_plan)

    case.phase = 'steady_reuse'
    current = case.call('b', 'check-use', old_use)
    assert current[0]['status'] == 'current'

    case.phase = 'source_correction'
    challenge, decision = case.challenge('source_challenge', 'a', 'departure')
    case.author('a', 'sources/reviewed.txt', NEW)
    proposal, candidate = case.propose('source_successor_proposal', 'a', 'admission', NEW,
                                       'sources/reviewed.txt', 'departure')
    binding, incorporation = case.install('source_successor', 'a', proposal, candidate,
                                         'admission', 'sources/reviewed.txt')
    case.resolve('source_resolution', 'a', challenge, decision, binding, incorporation)
    receipt = case.artifact('source_corrected_receipt', 'a', 'read', 'a:admission', *SCOPE)
    corrected_capsule, corrected = case.export('corrected-transfer', 'a', receipt)
    assert corrected['events'][:len(first['events'])] == first['events']

    case.phase = 'destination_correction'
    updated = case.artifact('corrected_import', 'b', 'import-transfer', corrected_capsule, *ATTR)
    stale = case.call('b', 'check-use', old_use, expected=1)
    assert b'known-superseded' in stale.stderr and b'departure' in stale.stderr
    assert projects['a'].encode() in stale.stderr
    case.call('b', 'show', old_use)
    challenge, decision = case.challenge('local_challenge', 'b', 'local-departure')
    tracked = case.artifact('tracked_report', 'b', 'track-publication', challenge,
                            '--project', 'b', '--path', 'exports/report.txt', '--use', old_use, *ATTR)
    case.author('b', 'sources/reviewed.txt', NEW)
    proposal, candidate = case.propose('local_successor_proposal', 'b', 'local-admission', NEW,
                                       'sources/reviewed.txt', 'local-departure')
    case.artifact('corrected_link', 'b', 'link-transfer', proposal, '--import', updated,
                  '--origin', f'{projects["a"]}:admission', '--predecessor',
                  f'{projects["a"]}:departure=b:local-departure', *ATTR)
    binding, incorporation = case.install('local_successor', 'b', proposal, candidate,
                                         'local-admission', 'sources/reviewed.txt')
    case.resolve('local_resolution', 'b', challenge, decision, binding, incorporation)
    new_plan = 'Plan: prepare for the 16:00 admission cutoff; confirm later departure separately.\n'
    case.author('b', 'knowledge/plan-next.md', canonical('plan-next', new_plan, 'plan'))
    case.bind('new_plan_binding', 'b', 'plan-next', 'sources/planning.txt', 'local-admission')
    new_receipt, new_use = case.use('new_use', 'b', 'plan-next')
    case.call('b', 'check-use', new_use)

    case.phase = 'reflection'
    before_address = case.call('b', 'reconcile', challenge)[0]
    assert before_address['review'] == 'resolved'
    assert before_address['uses'][0]['id'] == old_use
    assert before_address['uses'][0]['addresses'] == []
    address = case.artifact('address', 'b', 'address', challenge, '--decision', decision,
                            '--old-use', old_use, '--new-use', new_use, *ATTR)
    case.author('b', 'exports/report.txt', new_plan)
    reflection = case.artifact('reflection', 'b', 'reflect', tracked, '--address', address, *ATTR)
    reconciled = case.call('b', 'reconcile', challenge)[0]
    assert reconciled['review'] == 'resolved' and reconciled['decision'] == decision
    assert reconciled['resolutions'] == [case.handles['local_resolution']['id']]
    effect, = reconciled['uses']
    assert effect['id'] == old_use and effect['observation']['status'] == 'review required'
    assert effect['addresses'] == [dict(id=address, new_use=new_use,
                                       observation=dict(reason='', status='current'))]
    publication, = effect['publications']
    assert publication['id'] == tracked
    assert publication['reflections'] == [dict(id=reflection, observation=dict(reason='', status='current'))]
    assert publication['observation']['status'] == 'stale snapshot'

    case.phase = 'final_handoff'
    final_path, final_capsule = case.export('final-planning-transfer', 'b', new_receipt)
    final_import = case.artifact('final_import', 'c', 'import-transfer', final_path, *ATTR)
    inventory = case.call('c', 'show-transfer', final_import)[0]['inventory']
    assert inventory['live_origin'] == 'not-checked' and inventory['local_links'] == []
    assert any(row['identity']['unit'] == 'plan-next' for row in inventory['units'])
    retained = case.call('c', 'show', final_import)[0]['artifact']['payload']['capsule']
    assert retained == final_capsule
    events = {event['id']: event for event in retained['events']}
    selected = events[retained['receipt']]['payload']
    assert retained['receipt'] == new_receipt
    assert selected['root'] == dict(project=projects['b'], unit='plan-next')
    units = {unit['identity']['unit']: unit for unit in selected['content']['units']}
    assert set(units) == {'plan-next', 'local-admission'}
    assert units['plan-next']['text'] == canonical('plan-next', new_plan, 'plan')
    assert units['local-admission']['text'] == canonical('local-admission', NEW, 'local-departure')
    linked = events[case.handles['corrected_link']['id']]['payload']
    origin = linked['origin']
    assert origin['workspace'] == corrected['workspace']
    assert origin['identity'] == dict(project=projects['a'], unit='admission')
    assert origin['binding'] == case.handles['source_successor_binding']['id']
    predecessor, = linked['predecessors']
    assert predecessor['mode'] == 'mapped'
    assert predecessor['origin'] == dict(project=projects['a'], unit='departure')
    assert predecessor['local'] == dict(project=projects['b'], unit='local-departure')
    material = json.loads(linked['inspection_text'])['review']['material']
    source_unit, = material['units']
    assert source_unit['workspace'] == corrected['workspace']
    assert source_unit['identity'] == origin['identity']
    assert source_unit['file']['text'] == canonical('admission', NEW, 'departure')
    old_source, = material['predecessor_files']
    assert old_source['workspace'] == corrected['workspace']
    assert old_source['identity'] == predecessor['origin']
    assert old_source['file']['text'] == canonical('departure', OLD)
    assert events[updated]['payload']['capsule'] == corrected
    assert events[imported]['payload']['capsule'] == first
    assert selected['transfer']['frontier']['origins'] == [
        dict(workspace=corrected['workspace'], head=corrected['events'][-1]['id'])]
    assert selected['transfer']['frontier']['links'] == [
        dict(workspace=retained['workspace'], link=case.handles['corrected_link']['id'])]
    case.phase = 'retention_audit'
    for key, handle in case.handles.copy().items():
        artifact = case.call(handle['workspace'], 'show', handle['id'])[0]['artifact']
        assert artifact['id'] == handle['id'], key
    assert (case.roots['a'] / 'knowledge/departure.md').read_text() == canonical('departure', OLD)
    assert (case.roots['b'] / 'knowledge/local-departure.md').read_text() == canonical('local-departure', OLD)
    assert (case.roots['b'] / 'knowledge/plan.md').read_text() == canonical('plan', old_plan)
    assert (case.roots['b'] / 'exports/report.txt').read_text() == new_plan
    summary = {}
    for phase in dict.fromkeys(row['phase'] for row in case.records):
        rows = [row for row in case.records if row['phase'] == phase]
        summary[phase] = dict(commands=len(rows), expected_refusals=sum(row['expected'] != 0 for row in rows),
                              stdout_bytes=sum(row['stdout_bytes'] for row in rows),
                              stderr_bytes=sum(row['stderr_bytes'] for row in rows),
                              cli_elapsed_seconds=sum(row['elapsed_seconds'] for row in rows))
    summary['setup']['initialization_commands'] = len(case.initializations)
    summary['setup']['initialization_stdout_bytes'] = sum(r['stdout_bytes'] for r in case.initializations)
    summary['setup']['initialization_stderr_bytes'] = sum(r['stderr_bytes'] for r in case.initializations)
    summary['setup']['initialization_elapsed_seconds'] = sum(r['elapsed_seconds'] for r in case.initializations)
    (base / 'results.json').write_text(json.dumps(dict(phases=summary, commands=len(case.records),
        adopter_unchanged=all(row['adopter_unchanged'] for row in case.records),
        human_interventions=0, decisions='Predetermined synthetic judgments; not empirical usefulness.',
        handles=case.handles, projects=projects), indent=2) + '\n')
    return case


def test_complete_everyday_correction_lifecycle(tmp_path):
    run_case(tmp_path / 'everyday')


def test_everyday_playbook_links_contracts_and_distinguishes_fixture_claims():
    path = REPO / 'docs/reference/everyday-workflow.md'
    text = path.read_text()
    for phrase in ('Available since 2.3.0', '2.2.0', 'predetermined synthetic judgments',
                   'whole-history', '8 MiB', '32 MiB', 'not model tokens'):
        assert phrase.lower() in ' '.join(text.lower().split())
    for target in re.findall(r'\]\(([^)]+)\)', text):
        assert (path.parent / target.split('#')[0]).exists()
    assert 'docs/reference/everyday-workflow.md' in (REPO / 'README.md').read_text()
    assert '(everyday-workflow.md)' in (REPO / 'docs/reference/consultation.md').read_text()
