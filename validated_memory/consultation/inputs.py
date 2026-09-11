"""Bounded, descriptor-relative capture of enrolled canonical inputs."""

import os
import stat
from contextlib import ExitStack, contextmanager

from .. import extension
from ..contract import validate_documents
from ..derive import effective_states
from ..findings import ERROR
from . import model as m


def _node(info):
    return {'device': info.st_dev, 'inode': info.st_ino}


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _directory(path, parent=None):
    """Open every component without following a link, retaining only the leaf."""
    fd = os.open('/' if parent is None else '.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        for part in path.split('/'):
            if part in ('', '.'):
                continue
            m.require(part != '..', f'{path}: traversal is unsupported; supply a canonical path')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def root_identity(path):
    """Return the canonical nonsymlink directory location and retained node."""
    value = os.fspath(path)
    absolute = value if os.path.isabs(value) else os.path.join(os.getcwd(), value)
    try:
        with _directory(absolute) as fd:
            canonical = os.path.normpath(absolute)
            m.path(canonical, root=True)
            return canonical, _node(os.fstat(fd))
    except OSError as error:
        raise m.Refusal(f'{value}: root unavailable or unsafe; restore the root or explicitly relocate') from error


def _schema_path(declaration, root, root_fd):
    """Resolve supported local schema spellings without skipping unsafe ancestors."""
    m.text(declaration, 4096)
    m.require('\x00' not in declaration and '\\' not in declaration,
              'invalid schema path; declare an in-root regular schema')
    if os.path.isabs(declaration):
        m.require(declaration.startswith(root + '/'),
                  f'{declaration}: schema must stay inside registered root; declare a local schema')
        declaration = declaration[len(root) + 1:]
    parts = declaration.split('/')
    resolved = []
    for index, part in enumerate(parts):
        if part in ('', '.'):
            continue
        if part == '..':
            m.require(resolved, 'schema traversal escapes registered root; declare a local schema')
            resolved.pop()
            continue
        resolved.append(part)
        if index < len(parts) - 1:
            with _directory('/'.join(resolved), root_fd):
                pass
    canonical = '/'.join(resolved)
    m.path(canonical)
    return canonical


class _Project:
    def __init__(self, fd, registration, budget):
        self.fd = fd
        self.registration = registration
        self.budget = budget
        self.files = {}
        self.stamps = {}

    def read(self, path):
        try:
            return self._read(path)
        except (OSError, UnicodeError) as error:
            raise m.Refusal(f"{self.registration['root']}/{path}: input unavailable or invalid UTF-8; "
                            'inspect/restore the input, or review-support with its replacement path '
                            'if the canonical claim is unchanged') from error

    def _read(self, path):
        m.path(path)
        if path in self.files:
            return self.files[path]
        self.budget[0] += 1
        m.require(self.budget[0] <= 4096, '4096 input file limit exceeded; narrow enrolled scope')
        parent, _, leaf = path.rpartition('/')
        with _directory(parent, self.fd) as directory:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(fd)
                m.require(stat.S_ISREG(before.st_mode), f'{path}: input must be a regular nonsymlink file; inspect inputs')
                m.require(before.st_size <= 1048576, f'{path}: 1 MiB file limit exceeded; narrow enrolled scope')
                chunks = []
                size = 0
                while True:
                    chunk = os.read(fd, min(65536, 1048577 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    m.require(size <= 1048576, f'{path}: 1 MiB file limit exceeded; narrow enrolled scope')
                m.require(_stamp(before) == _stamp(os.fstat(fd)), f'{path}: input changed during capture; retry with stable inputs')
            finally:
                os.close(fd)
        raw = b''.join(chunks)
        self.budget[1] += len(raw)
        m.require(self.budget[1] <= 16777216, '16 MiB aggregate input limit exceeded; narrow enrolled scope')
        text = raw.decode('utf-8')
        result = dict(path=path, sha256=m.byte_digest(raw), size=len(raw), text=text)
        self.files[path] = result
        self.stamps[path] = _stamp(before)
        return result

    def membership(self):
        result = {}
        pending = ['knowledge']
        while pending:
            path = pending.pop()
            m.path(path)
            with _directory(path, self.fd) as fd:
                before = os.fstat(fd)
                entries = sorted(os.listdir(fd))
                for name in entries:
                    child = path + '/' + name
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    m.require(not stat.S_ISLNK(info.st_mode), f'{child}: symlink in knowledge tree; use nonsymlink canonical inputs')
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(child)
                    elif name.endswith('.md'):
                        m.require(stat.S_ISREG(info.st_mode), f'{child}: canonical input must be regular; inspect inputs')
                        result[child] = _stamp(info)
                        m.require(len(result) <= 4096, '4096 input file limit exceeded; narrow enrolled scope')
                m.require(_stamp(before) == _stamp(os.fstat(fd)), f'{path}: membership changed; retry with stable inputs')
        return result

    def verify(self, membership):
        m.require(self.membership() == membership, 'knowledge membership or metadata changed; retry with stable inputs')
        for path, expected in self.stamps.items():
            parent, _, leaf = path.rpartition('/')
            with _directory(parent, self.fd) as fd:
                actual = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            m.require(_stamp(actual) == expected, f'{path}: input metadata changed; retry with stable inputs')
        location, identity = root_identity(self.registration['root'])
        m.require(location == self.registration['root'] and identity == self.registration['root_identity']
                  and _node(os.fstat(self.fd)) == identity,
                  f'{location}: root identity changed; restore or explicitly relocate')


def capture(state, registrations=None, support_overrides=None, insertion=None):
    """Capture the complete prospective inventory without consulting stored heads."""
    registrations = (registrations if registrations is not None else
                     {key: event['payload'] for key, event in state.registrations.items()})
    support_overrides = support_overrides or {}
    m.require(len(registrations) <= 16, '16 project limit exceeded; narrow enrolled scope')
    projects = {}
    budget = [0, 0]
    captures = []
    location = 'enrolled inputs'
    try:
        with ExitStack() as stack:
            for project, registration in sorted(registrations.items()):
                location = registration['root']
                canonical, identity = root_identity(location)
                m.require(canonical == location and identity == registration['root_identity'],
                          f'{location}: registered root identity changed; restore or explicitly relocate')
                fd = stack.enter_context(_directory(location))
                source = _Project(fd, registration, budget)
                config = source.read('validated-memory.md')
                schema_path = extension.declared_schema(config['path'], config['text'])
                if schema_path is not None:
                    schema_path = _schema_path(schema_path, location, fd)
                schema = source.read(schema_path) if schema_path is not None else None
                declared = extension.extension_from_texts(config['path'], config['text'],
                            schema['path'] if schema else None, schema['text'] if schema else None)
                membership = source.membership()
                knowledge = [source.read(path) for path in sorted(membership)]
                documents = [(item['path'], item['text']) for item in knowledge]
                errors = [finding.render() for finding in validate_documents(documents, declared)
                          if finding.severity == ERROR]
                m.require(not errors, f'{location}: canonical validation failed: ' + '; '.join(errors) + '; repair canonical inputs')
                before_states = effective_states(documents)
                if insertion and insertion['identity']['project'] == project:
                    candidate = insertion['unit']
                    m.require(candidate['path'] not in membership and insertion['identity']['unit'] not in before_states,
                              'proposal destination path/ID exists; renew its exact installed proposal')
                    m.require(not os.path.lexists(os.path.join(location, candidate['path'])), 'proposal destination already exists')
                    knowledge.append(candidate)
                    knowledge.sort(key=lambda item: item['path'])
                    source.files[candidate['path']] = candidate
                    budget[0] += 1
                    budget[1] += candidate['size']
                    m.require(budget[0] <= 4096 and budget[1] <= 16777216, 'prospective input bound exceeded')
                    documents.append((candidate['path'], candidate['text']))
                errors = [finding.render() for finding in validate_documents(documents, declared)
                          if finding.severity == ERROR]
                m.require(not errors, f'{location}: canonical validation failed: ' + '; '.join(errors) + '; repair canonical inputs')
                states = effective_states(documents)
                units = {m.frontmatter(item['text'])['id']: item for item in knowledge}
                units = {key: dict(file=units[key], data=data, state=status)
                         for key, (data, status) in states.items()}
                inventory = dict(version=1, project=project, root=location, root_identity=identity,
                                 config=m.revision(config), schema=m.revision(schema) if schema else None,
                                 knowledge=[m.revision(item) for item in knowledge], support=[])
                projects[project] = dict(inventory=inventory, files=source.files, units=units, before_states=before_states)
                captures.append((source, membership))
            for source, _membership in captures:
                project = source.registration['project']
                location = source.registration['root']
                units = projects[project]['units']
                support_paths = set()
                for pair in set(state.bindings) | set(support_overrides):
                    if pair[0] != project or pair[1] not in units or units[pair[1]]['state'] != 'active':
                        continue
                    paths = (support_overrides[pair] if pair in support_overrides else
                             [item['path'] for item in state.bindings[pair]['payload']['support']])
                    support_paths.update(paths)
                support = [source.read(path) for path in sorted(support_paths)]
                projects[project]['inventory']['support'] = [m.revision(item) for item in support]
            for source, membership in captures:
                location = source.registration['root']
                source.verify(membership)
    except (OSError, UnicodeError, extension.ExtensionError) as error:
        raise m.Refusal(f'{location}: input capture failed: {error}; inspect and restore complete valid UTF-8 inputs') from error
    return projects


def external(path, maximum=1048576):
    """Capture one supplied file without following any directory or leaf link."""
    supplied = os.fspath(path)
    absolute = supplied if os.path.isabs(supplied) else os.path.join(os.getcwd(), supplied)
    parent, leaf = os.path.split(absolute)
    root, node = root_identity(parent)
    with _directory(root) as fd:
        source = _Project(fd, {'root': root, 'root_identity': node}, [0, 0])
        value = source.read(leaf)
        m.require(value['size'] <= maximum, 'supplied file exceeds byte bound')
        actual = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        m.require(_stamp(actual) == source.stamps[leaf], 'supplied file changed; retry')
    return absolute, value


def publication(state, project, path, projects):
    """Capture a declared publication, never canonical or private store files."""
    m.path(path)
    inv = projects[project]['inventory']
    excluded = {'knowledge', 'memory', '.git', '.validated-memory'}
    m.require(path.split('/')[0] not in excluded and path != 'validated-memory.md'
              and (inv['schema'] is None or path != inv['schema']['path']),
              'publication path is canonical/configuration/private; choose a downstream artifact')
    root = state.registrations[project]['payload']['root']
    absolute, value = external(os.path.join(root, path))
    m.require(absolute not in [state.store_path + suffix for suffix in ('', '-journal', '-wal', '-shm')],
              'publication cannot be workspace storage')
    return dict(value, path=path)
