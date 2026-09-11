"""Argument contract for explicit contribution and correction operations."""

from . import model as m

OPERATIONS = ('upgrade', 'submit', 'renew', 'challenge', 'inspect', 'decide', 'incorporate',
              'resolve', 'reconcile', 'address', 'track-publication', 'reflect')


def parser(commands, single):
    for operation in OPERATIONS:
        sub = commands.add_parser(operation)
        if operation == 'submit':
            sub.add_argument('alias')
            sub.add_argument('candidate_file')
            sub.add_argument('--path', action=single, required=True)
            sub.add_argument('--authority', action=single, required=True)
        elif operation == 'challenge':
            sub.add_argument('target')
            sub.add_argument('--statement', action=single, required=True)
            sub.add_argument('--kind', action=single, required=True, choices=('factual', 'policy'))
        elif operation != 'upgrade':
            sub.add_argument('handle')
        if operation in ('submit', 'renew'):
            sub.add_argument('--support', action='append', required=operation == 'submit', default=None)
            sub.add_argument('--reference', action='append', default=None)
        if operation in ('submit', 'renew', 'challenge'):
            sub.add_argument('--scope', action='append', required=operation != 'renew', default=None)
        if operation == 'inspect':
            sub.add_argument('--max-bytes', action=single, type=int, default=65536)
        if operation in ('decide', 'resolve'):
            sub.add_argument('--inspection', action=single, required=True)
        if operation == 'decide':
            sub.add_argument('--outcome', action=single, required=True, choices=('accept', 'reject', 'defer'))
            sub.add_argument('--prior', action=single)
        if operation in ('incorporate', 'resolve', 'address'):
            sub.add_argument('--decision', action=single, required=True)
        if operation == 'resolve':
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument('--incorporation', action=single)
            group.add_argument('--review', action=single)
        if operation == 'address':
            sub.add_argument('--old-use', action=single, required=True)
            sub.add_argument('--new-use', action=single, required=True)
            sub.add_argument('--mode', action=single, default='replacement', choices=('replacement', 'dependency-removed'))
        if operation == 'track-publication':
            sub.add_argument('--project', action=single, required=True)
            sub.add_argument('--path', action=single, required=True)
            sub.add_argument('--use', action=single, required=True)
        if operation == 'reflect':
            sub.add_argument('--address', action=single, required=True)
        if operation not in ('upgrade', 'inspect', 'reconcile'):
            sub.add_argument('--actor', action=single, required=True)
            sub.add_argument('--reason', action=single, required=True)
        sub.set_defaults(consultation_parser=sub)


def normalize(args):
    if args.operation == 'renew':
        explicit = any(getattr(args, field) is not None for field in ('support', 'reference', 'scope'))
        m.require(not explicit or (args.support and args.scope), 'renew complete declaration requires --support and --scope; omitted references mean empty')
        args.explicit_declaration = explicit
    for field in ('support', 'reference', 'scope'):
        if hasattr(args, field) and getattr(args, field) is None:
            if field == 'scope':
                delattr(args, field)
            else:
                setattr(args, field, [])
    for field in ('inspection', 'decision', 'incorporation', 'review', 'old_use', 'new_use', 'use', 'address'):
        if getattr(args, field, None) is not None:
            m.sha(getattr(args, field))
    if hasattr(args, 'path'):
        m.path(args.path)
    if args.operation == 'submit':
        m.require(args.path.startswith('knowledge/') and args.path.endswith('.md'), '--path requires canonical knowledge Markdown')
    if hasattr(args, 'project'):
        m.name(args.project)
    if args.operation == 'inspect':
        m.integer(args.max_bytes, 2048, 1048576)
