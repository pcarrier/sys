#!/usr/bin/env python3
"""Pure sufficient systemd v261 START-effect proof; never controls a manager.

This is NOT a generic active-frontier theorem, transaction emulator, or atomic
manager exclusion. Under an explicit cooperative control/filesystem window,
construct the ENTIRE prospective typed job graph before dropping anything.
Drop only units whose ALL prospective job types are first-pass redundant.
Reject every remaining dependency cycle (including disconnected orphan SCCs),
then iteratively GC indegree-zero nonanchors. Accept only the sole START anchor.
Mixed START/STOP barriers are never inferred from an active state. There is no
post-merge redundancy/GC shortcut: v261's last redundancy pass has no later GC.

Primary source: systemd/systemd tag v261, src/core/{transaction.c,job.c,
unit-dependency-atom.c,unit.c,device.c,swap.c}. Transaction lines 1035-1157,
292-325,514-543,744-789; job lines443-479. Exact upstream byte hashes below.
The host's 261.2 build/patch identity MUST be separately reviewed and pinned by
an operator lease. Version strings alone do not establish patch equivalence.
Fail/HOLD on unsupported types, metadata, states, lookup errors or bounds.
"""
import hashlib
import json
import re

SCHEMA = 'systemd-v261-sole-anchor-start-v1'
MAX_UNITS = 256
MAX_JOBS = 1024
MAX_EDGES = 8192
MAX_LOADED = 16384
SOURCE_SHA256 = {
    'transaction.c': '2b8ad73d0b34a10175530544e88d98343114fcbe50fa7047fb3224facc2b9ffa',
    'job.c': 'b53ad79e94d80980f6212f6c67770d1daad02d1ba5d23c0a8cbd831da911eca7',
    'unit-dependency-atom.c': 'ac00161e86f4522648d3986496b80d726836de41972456209c52ad2a171bf0f5',
    'unit.c': '51c0cadb42612bd074f67055a02909b850390e405dc26218e8203441d03e2df4',
    'device.c': '1e4352d2f24655c706ccf6a162345b13413180d35a2536bd5cb675d16934b693',
    'swap.c': 'ec67a334600979f80f7af84d841e5ee973ae22d2bc3c68f22bc424004819c360',
    'dbus-unit.c': '80e1b2328ab84338dab095a6496adbf1be5f47d0e7093b2089a347dbcded5a42',
    'dbus-device.c': 'd220f19d80f8c941e88c764d8487eb60e7e6ae20abb933ebc7774acd6adff766',
}
# All dependency atom classes in v261. Non-job-forming relations remain in the
# certificate; neither textual Requires nor source-file parsing is substituted.
RELATIONS = (
    'Requires', 'Requisite', 'Wants', 'BindsTo', 'PartOf', 'Upholds',
    'RequiredBy', 'RequisiteOf', 'WantedBy', 'BoundBy', 'UpheldBy', 'ConsistsOf',
    'Conflicts', 'ConflictedBy', 'Before', 'After', 'OnSuccess', 'OnSuccessOf',
    'OnFailure', 'OnFailureOf', 'Triggers', 'TriggeredBy', 'PropagatesReloadTo',
    'ReloadPropagatedFrom', 'PropagatesStopTo', 'StopPropagatedFrom',
    'JoinsNamespaceOf', 'SliceOf',
)
START_REQUIRED = ('Requires', 'BindsTo')
START_IGNORED = ('Wants', 'Upholds')
STOP_REQUIRED = ('RequiredBy', 'RequisiteOf', 'BoundBy', 'ConsistsOf')
UNIT_STRINGS = ('Id', 'Following', 'LoadState', 'ActiveState', 'SubState',
                'FragmentPath', 'FreezerState', 'FailureAction', 'SuccessAction',
                'StartLimitAction', 'JobTimeoutAction')
UNIT_BOOLS = ('StopWhenUnneeded', 'NeedDaemonReload', 'RefuseManualStart', 'Perpetual')
UNIT_ARRAYS = ('Names', 'DropInPaths', 'Conditions', 'Asserts', *RELATIONS)
STATES = {'active', 'inactive', 'failed'}  # reloading is redundant upstream, but HOLD here
TYPES = {'START', 'STOP', 'VERIFY', 'NOP'}


def require(value, message):
    if not value:
        raise RuntimeError('Effect proof HOLD: ' + message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unit_name(value):
    # Unit IDs are exact manager strings, never shell data. Literal whitespace,
    # quotes and Unicode are invalid unit IDs; their escaped spellings are valid.
    require(isinstance(value, str) and 0 < len(value.encode()) <= 255 and
            re.fullmatch(r'[A-Za-z0-9:_.@\\-]+\.(?:service|target|mount|slice|socket|device|swap|timer|path|automount|scope)', value),
            'invalid exact unit ID')
    return value


def validate_unit(value):
    require(isinstance(value, dict), 'missing unit object')
    for key in UNIT_STRINGS:
        require(isinstance(value.get(key), str), 'missing typed string ' + key)
    for key in UNIT_BOOLS:
        require(type(value.get(key)) is bool, 'missing typed boolean ' + key)
    for key in UNIT_ARRAYS:
        require(isinstance(value.get(key), list), 'missing typed array ' + key)
        if key not in ('DropInPaths', 'Conditions', 'Asserts'):
            require(len(value[key]) == len(set(value[key])), 'duplicate relation/name ' + key)
            for name in value[key]:
                unit_name(name)
    unit_name(value['Id'])
    require(value['Id'] in value['Names'], 'canonical ID absent from aliases')
    require(isinstance(value.get('FollowingSet'), list) and
            len(value['FollowingSet']) == len(set(value['FollowingSet'])), 'missing bounded follow set')
    for name in value['FollowingSet']:
        unit_name(name)
    require(value.get('Job') == [0, '/'], 'loaded unit has existing job of ANY type')
    require(value['FreezerState'] == 'running' and not value['NeedDaemonReload'],
            'frozen/reload-needed unit')
    require(value['ActiveState'] in STATES, 'unstable/unmodeled active state')
    if value['Following']:
        unit_name(value['Following'])
    return value


def reject_anchor_handlers(unit):
    # Only the final executing anchor is subject to runtime-side-effect checks.
    # Boot-node handlers behind proven barriers never execute and are not run.
    for relation in ('OnFailure', 'OnSuccess', 'Triggers', 'TriggeredBy', 'Upholds',
                     'UpheldBy', 'RequiredBy', 'RequisiteOf', 'BoundBy', 'ConsistsOf',
                     'PropagatesStopTo', 'PropagatesReloadTo'):
        require(not unit[relation], 'anchor handler/extra-job effect ' + relation)
    for action in ('FailureAction', 'SuccessAction', 'StartLimitAction', 'JobTimeoutAction'):
        require(unit[action] == 'none', 'anchor manager action ' + action)
    require(not unit['StopWhenUnneeded'] and not unit['RefuseManualStart'],
            'anchor can auto-stop or refuses explicit start')
    require(not unit['Conditions'] and not unit['Asserts'], 'anchor conditional activation unsupported')


def closure(anchor, fetch, ignored_not_found=()):
    """Build complete bounded typed graph. fetch must supply stable typed metadata.

    ignored_not_found is an explicitly reviewed *filesystem-window* allowlist,
    not permission to ignore unknown units. Only exact FileNotFound load errors
    on Wants/Upholds START edges can be omitted. Required/VERIFY/STOP lookup
    failures always HOLD, including a later required edge to the same name.
    Exception: STOP on a typed NOT_FOUND/NoSuchUnit unit is a KNOWN rule, not a
    lookup omission: v261 lines975-997 skip load validation/retry for STOP, and
    unit.c3037-3040 permits STOP unless Perpetual=true. Retain its entire STOP
    dependency/follow-set closure. Perpetual STOP has known EBADR and is skipped.
    PropagatesStopTo graceful handling depends on traversal order and can yield
    RESTART. If reached by STOP, HOLD instead of guessing that order.
    """
    unit_name(anchor)
    ignored_not_found = set(ignored_not_found)
    for name in ignored_not_found:
        unit_name(name)
    units, aliases, jobs, edges, omissions = {}, {}, set(), set(), []
    pending = [(None, anchor, 'START', False, 'anchor')]

    def get(name):
        unit_name(name)
        if name in aliases:
            return units[aliases[name]]
        value = validate_unit(fetch(name))
        ident = value['Id']
        if ident in units:
            require(units[ident] == value, 'alias/metadata equivocation')
        else:
            require(len(units) < MAX_UNITS, 'unit closure bound exceeded')
            units[ident] = value
        require(name in value['Names'], 'requested name not a manager alias')
        for alias in value['Names']:
            require(alias not in aliases or aliases[alias] == ident, 'alias collision')
            aliases[alias] = ident
        return value

    while pending:
        parent, name, kind, ignored, relation = pending.pop()
        value = get(name)
        ident = value['Id']
        if value['LoadState'] != 'loaded':
            known_not_found = (value['LoadState'] == 'not-found' and value['ActiveState'] == 'inactive' and
                               isinstance(value.get('LoadError'), list) and len(value['LoadError']) == 2 and
                               value['LoadError'][0] == 'org.freedesktop.systemd1.NoSuchUnit')
            known_enoent = (ignored and kind == 'START' and ident in ignored_not_found and known_not_found)
            known_stop = kind == 'STOP' and known_not_found
            require(known_enoent or known_stop, 'unknown/unloaded dependency ' + ident)
            if known_enoent:
                omissions.append({'from': list(parent) if parent else None, 'unit': ident,
                                  'relation': relation, 'rule': 'ignored-START-ENOENT-v261-1065:1075'})
                continue
        if kind == 'STOP' and value['Perpetual']:
            require(parent is not None, 'perpetual anchor STOP not applicable')
            omissions.append({'from': list(parent), 'unit': ident, 'relation': relation,
                              'rule': 'STOP-EBADR-Perpetual-unit.c3037:3040'})
            continue
        require(kind in TYPES, 'unmodeled restart/reload job')
        job = (ident, kind)
        if parent is not None:
            edges.add((parent, job))
            require(len(edges) <= MAX_EDGES, 'job edge bound exceeded')
        if job in jobs:
            continue
        require(len(jobs) < MAX_JOBS, 'prospective job bound exceeded')
        jobs.add(job)
        if kind == 'NOP':
            continue  # upstream returns before following/dependency expansion
        for follower in value['FollowingSet']:
            pending.append((job, follower, kind, True, 'FollowingSet'))
        if kind == 'START':
            for key in START_REQUIRED + START_IGNORED:
                for other in value[key]:
                    pending.append((job, other, 'START', key in START_IGNORED, key))
            for other in value['Requisite']:
                pending.append((job, other, 'VERIFY', False, 'Requisite'))
            for key in ('Conflicts', 'ConflictedBy'):
                for other in value[key]:
                    pending.append((job, other, 'STOP', key == 'ConflictedBy', key))
        elif kind == 'STOP':
            for key in STOP_REQUIRED:
                for other in value[key]:
                    pending.append((job, other, 'STOP', False, key))
            require(not value['PropagatesStopTo'], 'graceful STOP can induce RESTART/unmodeled order')
        # VERIFY has no dependency handling, but following expansion precedes it.

    canonical_anchor = aliases[anchor]
    require((canonical_anchor, 'START') in jobs, 'anchor omitted')
    return {'units': units, 'aliases': aliases, 'jobs': jobs, 'edges': edges,
            'anchor': (canonical_anchor, 'START'), 'omissions': omissions}


def redundant(kind, state):
    if kind in ('START', 'VERIFY'):
        return state == 'active'
    if kind == 'STOP':
        return state in ('inactive', 'failed')
    return kind == 'NOP'


def prove(anchor, fetch, existing_jobs=(), ignored_not_found=()):
    graph = closure(anchor, fetch, ignored_not_found)
    units, aliases = graph['units'], graph['aliases']
    for row in existing_jobs:
        require(isinstance(row, list) and len(row) == 6 and type(row[0]) is int and row[0] > 0 and
                all(isinstance(item, str) for item in row[1:]), 'malformed full ListJobs snapshot')
        require(aliases.get(row[1], row[1]) not in units,
                'existing job ANY type in COMPLETE effect closure: ' + row[1] + '/' + row[2])
    job_anchor = graph['anchor']
    reject_anchor_handlers(units[job_anchor[0]])
    require(units[job_anchor[0]]['ActiveState'] in ('inactive', 'failed'), 'anchor not stopped')
    per_unit = {}
    for name, kind in graph['jobs']:
        per_unit.setdefault(name, set()).add(kind)
    barriers = {name for name, kinds in per_unit.items() if name != job_anchor[0] and
                all(redundant(kind, units[name]['ActiveState']) for kind in kinds)}
    kept = {job for job in graph['jobs'] if job[0] not in barriers}
    kept_edges = {(src, dst) for src, dst in graph['edges'] if src in kept and dst in kept}
    # Reject mixed jobs BEFORE merge. Do not turn active START/STOP into a false
    # redundant START barrier based on a final merge/last-pass drop.
    require(all(len(kinds) == 1 for name, kinds in per_unit.items() if name not in barriers),
            'mixed unreducible prospective job types')
    # A DAG certificate, not mere anchor reachability. Disconnected inactive
    # A<->B after deleting active P retains object_list and survives real GC.
    incoming = {job: set() for job in kept}
    outgoing = {job: set() for job in kept}
    for src, dst in kept_edges:
        incoming[dst].add(src)
        outgoing[src].add(dst)
    degrees = {job: len(parents) for job, parents in incoming.items()}
    ready, order = sorted(job for job, degree in degrees.items() if degree == 0), []
    while ready:
        job = ready.pop()
        order.append(job)
        for child in outgoing[job]:
            degrees[child] -= 1
            if degrees[child] == 0:
                ready.append(child)
    require(len(order) == len(kept), 'residual dependency cycle, even disconnected from anchor')
    survivors, gc_order = set(kept), []
    for job in order:
        if job == job_anchor or any(parent in survivors for parent in incoming[job]):
            continue
        survivors.remove(job)
        gc_order.append(job)
    require(survivors == {job_anchor}, 'anchored nonanchor START/STOP/VERIFY/NOP effect')
    return {'schema': SCHEMA, 'anchor': list(job_anchor), 'primary_source_sha256': SOURCE_SHA256,
            'units': units, 'aliases': aliases,
            'prospective_jobs': [list(job) for job in sorted(graph['jobs'])],
            'prospective_edges': [[list(src), list(dst)] for src, dst in sorted(graph['edges'])],
            'first_pass_redundant_units': sorted(barriers),
            'residual_topological_order': [list(job) for job in order],
            'gc_order': [list(job) for job in gc_order],
            'final_effect': [list(job_anchor)], 'ignored_enoent': [entry for entry in graph['omissions']
                         if entry['rule'].startswith('ignored-START')],
            'not_applicable_stop': [entry for entry in graph['omissions'] if entry['rule'].startswith('STOP-EBADR')],
            'known_not_found_stop_units': sorted({name for name, kind in graph['jobs']
                         if kind == 'STOP' and units[name]['LoadState'] == 'not-found'})}
