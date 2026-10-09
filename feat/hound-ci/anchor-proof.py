#!/usr/bin/env python3
"""READ-ONLY per-slot pre-start proof for a manual or resumed slot start.

Reviewed form of the helper used for the 10-06 manual completion (parent
decision 04:13 UTC): after the unit links point at the new store units and the
holds are gone, slot N may be started by hand only once this prints
ANCHOR_PROOF_OK for it. It loads the cleared activate-cache-v2.py and
effect-proof.py from direct /nix/store paths (SHA-checked, compiled from the
checked bytes) and runs the activation's own checks:

* every slot is exact_loaded from its NEW store unit and argv, unheld;
* slots < N run with a NEW invocation (already started), slots >= N are stopped
  with their ORIGINAL invocation (from the drain manifest, passed explicitly);
* ONE validate_dependencies() pass, with slots < N tracked as started, must
  certify hound-ci-N as the sole START effect and bind exactly the lease's
  effect structure.

It writes nothing and starts nothing: no files, no systemd jobs, no reloads.
Only systemctl show and busctl reads run. The start itself stays a separate,
explicit `systemctl --job-mode=fail start -- hound-ci-N.service`.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType, SimpleNamespace

SOURCE_LIMIT = 1024 * 1024
STORE = Path('/nix/store')
SHA = re.compile('[0-9a-f]{64}')
INVOCATION = re.compile('[0-9a-f]{32}')


def refuse(message):
    raise RuntimeError('ANCHOR_PROOF_REFUSED ' + message)


def load_pinned(path, expected_sha, name):
    """Compile a direct /nix/store source from the bytes whose SHA was checked."""
    path = Path(path)
    if path.parent != STORE or not SHA.fullmatch(expected_sha or ''):
        refuse(f'{path}: a direct /nix/store source and a SHA-256 pin are required')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o222 or not 0 < meta.st_size <= SOURCE_LIMIT:
            refuse(f'{path}: not an immutable bounded regular file')
        data = os.read(fd, SOURCE_LIMIT + 1)
    finally:
        os.close(fd)
    if len(data) != meta.st_size or hashlib.sha256(data).hexdigest() != expected_sha:
        refuse(f'{path} differs from its cleared SHA')
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


class Bound:
    """The lease's structure SHA; records what validate_dependencies compared."""
    def __init__(self, expected):
        self.expected, self.actual = expected, None

    def __eq__(self, other):
        self.actual = other
        return other == self.expected

    __hash__ = None


def worker_values(act, slot, original):
    values = {}
    for number, store in act.UNITS.items():
        name = f'hound-ci-{number}.service'
        source = Path(store) / name
        argv, _ = act.unit_command(act.store_file(source))
        loaded = act.properties(name)
        act.exact_loaded(loaded, name, source, argv, False, running=number < slot)
        invocation = loaded['InvocationID']
        if number >= slot:
            act.require(invocation == original[number], f'{name} lost its ORIGINAL invocation: unknown start, HOLD')
        else:
            act.require(INVOCATION.fullmatch(invocation) is not None and invocation != original[number],
                        f'{name} is running but kept its ORIGINAL invocation')
        values[name] = loaded
    return values


def prove_anchor(act, effects, slot, original, structure, ignored_not_found):
    act.require(slot in act.UNITS, 'Unknown slot')
    act.require(set(original) == set(act.UNITS) and all(INVOCATION.fullmatch(original[n]) for n in original) and
                len(set(original.values())) == len(original), 'Four distinct ORIGINAL invocations required')
    act.require(SHA.fullmatch(structure) is not None, 'Lease effect structure SHA required')
    values = worker_values(act, slot, original)
    bound = Bound(structure)
    window = SimpleNamespace(value={'ignored_not_found': list(ignored_not_found), 'effect_structure_sha256': bound},
                             check=lambda: None)
    started = {f'hound-ci-{n}.service' for n in act.UNITS if n < slot}
    try:
        certificates = act.validate_dependencies(values, effects, window, started)
    except RuntimeError:
        if bound.actual is not None and bound.actual != structure:
            raise RuntimeError(f'Effect structure {bound.actual} differs from the lease {structure}') from None
        raise
    anchor = f'hound-ci-{slot}.service'
    act.require(certificates[anchor]['anchor'] == [anchor, 'START'] and
                certificates[anchor]['final_effect'] == [[anchor, 'START']],
                'Anchor not certified as the sole START effect')
    return {'unit': anchor, 'structure': bound.actual,
            'final_effect_sha256': hashlib.sha256(act.canonical(certificates[anchor]['final_effect'])).hexdigest()}


def originals(items):
    result = {}
    for item in items:
        match = re.fullmatch(r'([1-9])=([0-9a-f]{32})', item)
        if match is None or int(match[1]) in result:
            refuse(f'--original-invocation {item!r}: SLOT=32-hex, once per slot')
        result[int(match[1])] = match[2]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--slot', type=int, required=True)
    parser.add_argument('--activation-source', type=Path, required=True)
    parser.add_argument('--activation-sha256', required=True)
    parser.add_argument('--effect-source', type=Path, required=True)
    parser.add_argument('--effect-sha256', required=True)
    parser.add_argument('--structure-sha256', required=True, help="The held lease's effect_structure_sha256")
    parser.add_argument('--original-invocation', action='append', default=[], metavar='SLOT=ID',
                        help="Each slot's ORIGINAL InvocationID from the drain manifest (four times)")
    parser.add_argument('--ignored-not-found', action='append', default=[],
                        help="The lease's ignored_not_found entries")
    args = parser.parse_args(argv)
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode):
        refuse('run with the pinned Nix Python -I -B')
    act = load_pinned(args.activation_source, args.activation_sha256, 'anchor_proof_activation')
    effects = act.load_source(args.effect_source, args.effect_sha256, 'anchor_proof_effects')
    result = prove_anchor(act, effects, args.slot, originals(args.original_invocation), args.structure_sha256,
                          args.ignored_not_found)
    print('ANCHOR_PROOF_OK', result['unit'], 'structure', result['structure'],
          'final_effect_sha', result['final_effect_sha256'][:16], 'activation_sha', args.activation_sha256,
          'effect_sha', args.effect_sha256, flush=True)
    return result


if __name__ == '__main__':
    main()
