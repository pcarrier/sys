#!/usr/bin/env python3
"""Check the evaluated worker ExecStarts (JSON on stdin, from check.sh's nix eval).

hound: reservedMainSlots = 1 (hosts/hound.nix); zero: the default 0, which must
pass no --labels at all (the original four labels); two: a 2-slot reservation.
"""
import json
import re
import sys

SHARED = ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main']
MAIN = ['self-hosted', 'Linux', 'X64', 'hound-ci-main']
BASE = re.compile(r'/nix/store/[a-z0-9]{32}-hound-ci/bin/hound-ci worker --slot ([1-4]) --repo xmit-dev/ultimator '
                  r'--guest /nix/store/[a-z0-9]{32}-guest\.sh --image base-cache-v2\.qcow2')


def labels(command, slot):
    match = BASE.match(command)
    if match is None or match[1] != str(slot):
        sys.exit(f'Unexpected worker ExecStart for slot {slot}: {command}')
    rest = command[match.end():]
    if not rest:
        return None
    if not rest.startswith(' --labels '):
        sys.exit(f'Unexpected ExecStart suffix for slot {slot}: {rest!r}')
    return rest[len(' --labels '):].split(' ')


value = json.load(sys.stdin)
expected = {'hound': [SHARED, SHARED, SHARED, MAIN], 'zero': [None] * 4, 'two': [SHARED, SHARED, MAIN, MAIN]}
if set(value) != set(expected):
    sys.exit('Unexpected evaluation keys')
for key, wanted in expected.items():
    actual = [labels(command, slot) for slot, command in enumerate(value[key], 1)]
    if actual != wanted:
        sys.exit(f'{key}: slot labels {actual} != {wanted}')
print('SLOT_LABELS_OK hound=3x' + ','.join(SHARED) + '+1x' + ','.join(MAIN) + ' default=original-four')
