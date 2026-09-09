"""Independent scale/cap checks through CLI subprocesses; no package imports."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[3]
ENV = {**os.environ, 'PYTHONPATH': str(REPO), 'PYTHONDONTWRITEBYTECODE': '1'}


def invoke(root, query='needle'):
    with tempfile.TemporaryDirectory(prefix='recall-measure-') as directory:
        usage = Path(directory) / 'rss.txt'
        start = time.monotonic()
        result = subprocess.run(
            ['/usr/bin/time', '-f', '%M', '-o', str(usage), sys.executable,
             '-P', '-m', 'validated_memory', 'recall', query,
             '--layer', 'knowledge', '--format', 'json'],
            cwd=root, env=ENV, capture_output=True, timeout=120,
        )
        packet = json.loads(result.stdout)
        stats = {'exit': result.returncode, 'seconds': round(time.monotonic()-start, 4),
                 'peak_rss_kib': int(usage.read_text().splitlines()[-1]),
                 'stdout_bytes': len(result.stdout), 'coverage': packet['coverage']}
        assert b'Traceback' not in result.stderr, result.stderr
        assert len(result.stdout) <= 12288
        return result, packet, stats


def unit(path, identity, body='needle detail\n', supersedes=()):
    front = f'---\nid: {identity}\nevidence: hypothesis\n'
    if supersedes:
        front += 'supersedes:\n' + ''.join(f'  - {old}\n' for old in supersedes)
    path.write_text(front + '---\n' + body, encoding='utf-8')


report = {'python': platform.python_version(), 'platform': platform.platform(),
          'runtime_sha256': {name: hashlib.sha256((REPO / 'validated_memory' / name).read_bytes()).hexdigest()
                             for name in ('recall.py', 'recall_io.py')},
          'measurements': [], 'caps': []}
with tempfile.TemporaryDirectory(prefix='recall-scale-') as directory:
    root = Path(directory)
    knowledge = root / 'knowledge'
    knowledge.mkdir()
    previous = 0
    for count in (100, 1000, 10000, 20001):
        for number in range(previous, count):
            unit(knowledge / f'k{number:05d}.md', f'k{number:05d}')
        result, packet, stats = invoke(root)
        stats['documents'] = count
        if count <= 10000:
            assert result.returncode == 0 and packet['coverage']['matched'] == count, packet
            report['measurements'].append(stats)
        else:
            assert result.returncode == 1 and packet['results'] == [], packet
            assert '20,000' in str(packet['diagnostics']), packet
            report['caps'].append({'case': 'document_count', **stats})
        previous = count

with tempfile.TemporaryDirectory(prefix='recall-entry-cap-') as directory:
    root = Path(directory)
    knowledge = root / 'knowledge'
    knowledge.mkdir()
    for number in range(50001):
        (knowledge / f'f{number:05d}.txt').touch()
    result, packet, stats = invoke(root)
    assert result.returncode == 1 and packet['results'] == [], packet
    assert '50,000' in str(packet['diagnostics']), packet
    report['caps'].append({'case': 'entry_count', **stats})

with tempfile.TemporaryDirectory(prefix='recall-byte-cap-') as directory:
    root = Path(directory)
    knowledge = root / 'knowledge'
    knowledge.mkdir()
    for number in range(65):
        prefix = f'---\nid: k{number}\nevidence: hypothesis\n---\nneedle\n'
        (knowledge / f'k{number}.md').write_text(prefix + 'x' * (1048576-len(prefix)), encoding='utf-8')
    result, packet, stats = invoke(root)
    assert result.returncode == 1 and packet['results'] == [], packet
    assert '64 MiB' in str(packet['diagnostics']), packet
    report['caps'].append({'case': 'aggregate_bytes', **stats})

with tempfile.TemporaryDirectory(prefix='recall-redirect-cap-') as directory:
    root = Path(directory)
    knowledge = root / 'knowledge'
    knowledge.mkdir()
    old_ids = [f'old{number}' for number in range(318)]
    for identity in old_ids:
        unit(knowledge / f'{identity}.md', identity)
    for number in range(318):
        unit(knowledge / f'new{number}.md', f'new{number}', 'replacement\n', old_ids)
    result, packet, stats = invoke(root)
    assert result.returncode == 1 and packet['results'] == [], packet
    assert '100,000' in str(packet['diagnostics']), packet
    report['caps'].append({'case': 'redirect_associations', **stats})

print(json.dumps(report, indent=2))
