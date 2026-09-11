"""Chronological event replay; no filesystem access or mutable head storage."""

from . import model as m


class State:
    def __init__(self, workspace, store_path):
        m.uid(workspace)
        self.workspace = workspace
        self.store_path = store_path
        self.events = {}
        self.registrations = {}
        self.aliases = {}
        self.bindings = {}
        self.conflicts = {}
        self.choices = {}
        self.checkpoints = {}

    def event(self, handle, kinds=None):
        m.sha(handle)
        event = self.events.get(handle)
        m.require(event is not None and (kinds is None or event['kind'] in kinds),
                  f'{handle}: missing or wrong-kind artifact; inspect history')
        return event

    def resolve(self, qualified):
        parts = qualified.split(':')
        m.require(len(parts) == 2 and parts[0] in self.aliases,
                  f'{qualified}: unknown qualified identity; register project and inspect its units')
        value = {'project': self.aliases[parts[0]], 'unit': parts[1]}
        m.identity(value)
        return value

    def project(self, alias):
        m.require(alias in self.aliases, f'{alias}: unknown alias; register project')
        return self.aliases[alias]

    def heads(self):
        return dict(registrations=sorted(e['id'] for e in self.registrations.values()),
                    bindings=sorted(e['id'] for e in self.bindings.values()),
                    conflicts=sorted(self.conflicts),
                    choices=sorted(e['id'] for c, e in self.choices.items() if c in self.conflicts))

    def binding(self, identity):
        value = self.bindings.get(m.key(identity))
        m.require(value is not None, f'{identity}: no binding; bind the active unit first')
        return value

    def check_candidate(self, candidate):
        binding = self.event(candidate['binding'], ('binding', 'support-review'))
        m.require(binding == self.binding(candidate['identity']) and
                  binding['payload']['unit']['sha256'] == candidate['unit_sha256'],
                  f"{candidate['identity']}: stale conflict candidate; declare a conflict successor and choose")
        return binding

    def add(self, event):
        m.obj(event, 'sequence id kind prior payload created_at')
        m.integer(event['sequence'], 1, 10000)
        m.require(event['sequence'] == len(self.events) + 1, 'noncontiguous event sequence; restore intact history')
        m.sha(event['id'])
        m.require(event['id'] not in self.events, 'duplicate event ID; restore intact history')
        m.timestamp(event['created_at'])
        kind, p, prior = event['kind'], event['payload'], event['prior']
        m.payload(kind, p)
        if prior is not None:
            self.event(prior)
        m.require(event['id'] == m.event_id(kind, prior, p), 'event digest mismatch; restore intact history')
        if kind in ('registration', 'relocation'):
            self._registration(event)
        elif kind == 'checkpoint':
            registration = self.registrations.get(p['project'])
            m.require(registration is not None and registration['id'] == p['registration'],
                      'checkpoint references a stale registration')
            previous = self.checkpoints.get(p['registration'])
            m.require(prior == (previous['id'] if previous else None), 'checkpoint prior is not its head')
            self.checkpoints[p['registration']] = event
        elif kind in ('binding', 'support-review'):
            project = self.registrations.get(p['identity']['project'])
            m.require(project and p['authority'] == project['payload']['source'], 'binding authority/project mismatch')
            for ref in p['references']:
                m.require(ref['identity']['project'] in self.registrations, 'reference project not registered')
            pair = m.key(p['identity'])
            previous = self.bindings.get(pair)
            if kind == 'binding':
                m.require(prior is None and previous is None, 'duplicate first binding or invalid prior')
            else:
                m.require(previous and prior == previous['id'], 'support review prior is not binding head')
                m.require(p['unit'] == previous['payload']['unit'], 'support review changed canonical unit')
                m.require(m.semantic(p) != m.semantic(previous['payload']), 'redundant support review event')
            self.bindings[pair] = event
        elif kind == 'conflict':
            self._conflict(event)
        elif kind == 'choice':
            m.require(p['conflict'] in self.conflicts, 'choice references obsolete conflict')
            conflict = self.conflicts[p['conflict']]['payload']
            m.require(p['scope'] == conflict['scope'] and p['candidate'] in conflict['candidates'],
                      'choice candidate/scope does not match conflict')
            for candidate in conflict['candidates']:
                self.check_candidate(candidate)
            previous = self.choices.get(p['conflict'])
            m.require(prior == (previous['id'] if previous else None), 'choice prior is not its head')
            self.choices[p['conflict']] = event
        elif kind == 'receipt':
            m.require(prior is None, 'receipt cannot have prior')
            self._receipt(event)
        elif kind == 'use':
            m.require(prior is None, 'use cannot have prior')
            receipt = self.event(p['receipt'], ('receipt',))['payload']
            m.require(all(p[f] == receipt[f] for f in ('root', 'scope', 'snapshot_sha256', 'content_sha256')),
                      'use fields disagree with its receipt')
            m.require(receipt['snapshot']['heads'] == self.heads(), 'use committed against stale semantic heads')
        self.events[event['id']] = event

    def _registration(self, event):
        p, prior = event['payload'], event['prior']
        previous = self.registrations.get(p['project'])
        if event['kind'] == 'registration':
            m.require(prior is None and previous is None and p['alias'] not in self.aliases,
                      'duplicate project/alias or invalid registration prior')
            m.require(len(self.registrations) < 16, '16-project limit exceeded; narrow enrollment')
        else:
            m.require(previous and prior == previous['id'], 'relocation prior is not registration head')
            old = previous['payload']
            m.require(all(old[k] == p[k] for k in ('project', 'alias', 'source')) and old['root'] != p['root'],
                      'relocation changed identity/source or did not change root')
            checkpoint = self.event(p['checkpoint'], ('checkpoint',))
            m.require(self.checkpoints.get(previous['id']) == checkpoint and
                      checkpoint['payload']['project'] == p['project'] and
                      checkpoint['payload']['inventory_sha256'] == p['inventory_sha256'],
                      'relocation checkpoint is stale or inconsistent')
        m.require(not m.overlaps(self.store_path, p['root']), 'store overlaps adopter; select an outside store')
        for project, other in self.registrations.items():
            if project != p['project']:
                m.require(not m.overlaps(other['payload']['root'], p['root']), 'duplicate/nested adopter roots')
                m.require(other['payload']['root_identity'] != p['root_identity'], 'duplicate adopter root identity')
        self.registrations[p['project']] = event
        self.aliases[p['alias']] = p['project']

    def _conflict(self, event):
        p, prior = event['payload'], event['prior']
        for candidate in p['candidates']:
            binding = self.check_candidate(candidate)
            m.require(m.applies(binding['payload']['scope'], p['scope']), 'conflict scope outside candidate scope')
        if prior is None:
            m.require(not p['lineage'], 'first conflict cannot declare successor lineage')
        else:
            m.require(prior in self.conflicts, 'conflict prior is not current head')
            old = self.conflicts[prior]['payload']
            m.require(old['scope'] == p['scope'] and len(old['candidates']) == len(p['candidates']),
                      'conflict successor changes scope/cardinality')
            old_map = {m.key(c['identity']): c for c in old['candidates']}
            new_map = {m.key(c['identity']): c for c in p['candidates']}
            unchanged = set(old_map) & set(new_map)
            before_seen, after_seen = set(), set()
            for edge in p['lineage']:
                before, after = m.key(edge['before']), m.key(edge['after'])
                m.require(before in old_map and after in new_map and before not in unchanged
                          and after not in unchanged and before[0] == after[0]
                          and before not in before_seen and after not in after_seen,
                          'invalid one-to-one conflict replacement')
                before_seen.add(before)
                after_seen.add(after)
                proof = edge['proof']
                m.require(all(doc['path'].startswith('knowledge/') and doc['path'].endswith('.md')
                              for doc in proof), 'conflict lineage must retain canonical knowledge paths')
                m.require(proof[0] == self.binding(edge['after'])['payload']['unit'],
                          'conflict lineage new endpoint differs from its candidate binding')
                ids = [m.frontmatter(doc['text']).get('id') for doc in proof]
                m.require(ids[0] == after[1] and ids[-1] == before[1] and len(set(ids)) == len(ids)
                          and proof[0]['sha256'] == new_map[after]['unit_sha256']
                          and proof[-1]['sha256'] == old_map[before]['unit_sha256'],
                          'conflict lineage endpoint mismatch')
                for index, doc in enumerate(proof[:-1]):
                    m.require(ids[index + 1] in m.frontmatter(doc['text']).get('supersedes', []),
                              'conflict lineage lacks canonical supersession')
            m.require(before_seen == set(old_map) - unchanged and after_seen == set(new_map) - unchanged,
                      'incomplete conflict replacement mapping')
            del self.conflicts[prior]
        self.conflicts[event['id']] = event

    def _receipt(self, event):
        from .checks import content

        p = event['payload']
        snap = p['snapshot']
        m.require(snap['workspace'] == self.workspace and snap['heads'] == self.heads(),
                  'receipt historical workspace/head mismatch')
        m.require({x['project'] for x in snap['projects']} == set(self.registrations),
                  'receipt project membership mismatch')
        projects = {}
        total_files = total_bytes = 0
        for inventory in snap['projects']:
            project = inventory['project']
            registration = self.registrations[project]['payload']
            m.require(inventory['root'] == registration['root'] and
                      inventory['root_identity'] == registration['root_identity'],
                      'receipt registration inventory mismatch')
            files = {}
            for item in [inventory['config'], inventory['schema'], *inventory['knowledge'], *inventory['support']]:
                if item:
                    files[item['path']] = item
            total_files += len(files)
            total_bytes += sum(f['size'] for f in files.values())
            projects[project] = dict(inventory=inventory, files=files, units={})
        m.require(total_files <= 4096 and total_bytes <= 16 * 1024 * 1024, 'receipt inventory limits exceeded')
        for item in p['content']['units']:
            project = projects.get(item['identity']['project'])
            m.require(project is not None, 'receipt content project not enrolled')
            data = m.frontmatter(item['text'])
            m.require(data.get('id') == item['identity']['unit'], 'receipt canonical ID mismatch')
            project['units'][item['identity']['unit']] = dict(file={k: item[k] for k in ('path', 'sha256', 'text')},
                                                           data=data, state='active')
        expected = content(self, projects, p['root'], p['scope'], historical=True)
        m.require(expected == p['content'], 'receipt is not the complete declared closure')
        final = m.line('read', 'receipt recorded', id=event['id'])
        m.require(len((p['inspection_text'] + final).encode('utf-8')) <= p['max_bytes'],
                  'receipt exceeds its acquisition output bound')
