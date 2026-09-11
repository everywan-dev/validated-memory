"""Public arguments for explicit portable historical contributions."""

from . import model as m, transfer_values as tv

OPERATIONS = ('export-transfer', 'import-transfer', 'show-transfer', 'link-transfer', 'detach-transfer')


def parser(commands, single):
    for operation in OPERATIONS:
        sub = commands.add_parser(operation)
        sub.add_argument('capsule_file' if operation == 'import-transfer' else 'handle')
        if operation == 'export-transfer':
            sub.add_argument('--include-workspace-history', action='store_true', required=True)
            sub.add_argument('--assess', action='store_true')
        if operation != 'import-transfer':
            sub.add_argument('--max-bytes', action=single, type=int, default=tv.CAP if operation == 'export-transfer' else tv.WIRE)
        if operation == 'link-transfer':
            sub.add_argument('--import', dest='import_id', action=single, required=True)
            sub.add_argument('--origin', action=single, required=True)
            sub.add_argument('--dependency', action='append', default=[])
            sub.add_argument('--predecessor', action='append', default=[])
            sub.add_argument('--origin-only-predecessor', action='append', default=[])
        if operation == 'detach-transfer':
            sub.add_argument('--from', dest='from_link', action=single, required=True)
        if operation in ('link-transfer', 'detach-transfer'):
            sub.add_argument('--prior', action=single)
        if operation not in ('export-transfer', 'show-transfer'):
            sub.add_argument('--actor', action=single, required=True)
            sub.add_argument('--reason', action=single, required=True)
        sub.set_defaults(consultation_parser=sub)


def parse_origin(value):
    pieces = value.split(':')
    m.require(len(pieces) == 2, 'origin requires PROJECT_UUID:UNIT_ID')
    result = dict(project=pieces[0], unit=pieces[1])
    m.identity(result)
    return result


def normalize(args):
    for field in ('import_id', 'from_link'):
        if hasattr(args, field):
            m.sha(getattr(args, field))
    if hasattr(args, 'max_bytes'):
        m.integer(args.max_bytes, 2048, tv.CAP if args.operation == 'export-transfer' else tv.WIRE)
    if args.operation == 'link-transfer':
        args.origin = parse_origin(args.origin)
        for field in ('dependency', 'predecessor'):
            rows = getattr(args, field)
            m.require(len(rows) <= (64 if field == 'dependency' else 128) and len(set(rows)) == len(rows), 'mapping bound/duplicate')
            for item in rows:
                pieces = item.split('=')
                m.require(len(pieces) == 2, 'mapping requires ORIGIN_PROJECT:ID=DEST_ALIAS:ID')
                parse_origin(pieces[0])
                local = pieces[1].split(':')
                m.require(len(local) == 2, 'local mapping requires ALIAS:ID')
                m.name(local[0])
                m.pattern(local[1], m.ID_PATTERN.pattern)
        m.require(len(args.origin_only_predecessor) <= 128 and len(set(args.origin_only_predecessor)) == len(args.origin_only_predecessor), 'origin-only mapping bound/duplicate')
        for value in args.origin_only_predecessor:
            parse_origin(value)
