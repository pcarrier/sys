#!/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3
"""OLD-controller private mount-ns gate only. Never log arguments/stdin/env.

Bind over ONLY the old public gh wrapper in FOUR pidfd-pinned controller mount
namespaces. Delegate the immutable underlying ELF, preserving old wrapper's
telemetry default/argv0, stdin, environment and all non-JIT API calls.
"""
import os
import sys

OLD_GH = '/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/gh'
ORIGINAL = '/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/.gh-wrapped'
ROUTE = 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig'


def blocked(args):
    if not args or args[0] != 'api':
        return False
    method = None
    body = False
    endpoint = None
    i = 1
    value_options = {'-H', '--header', '-q', '--jq', '-t', '--template', '--hostname', '--cache', '--preview'}
    while i < len(args):
        value = args[i]
        if value in ('-X', '--method'):
            if i + 1 >= len(args):
                return False  # Original CLI reports malformed usage, not our gate.
            method = args[i + 1].upper(); i += 2; continue
        if value.startswith('--method='):
            method = value.split('=', 1)[1].upper()
        elif value.startswith('-X') and len(value) > 2:
            method = value[2:].upper()
        elif value in ('-F', '-f', '--field', '--raw-field', '--input'):
            body = True; i += 2; continue
        elif value.startswith(('--field=', '--raw-field=', '--input=')) or (value.startswith(('-F', '-f')) and len(value) > 2):
            body = True
        elif value in value_options:
            i += 2; continue
        elif not value.startswith('-') and endpoint is None:
            endpoint = value
        i += 1
    normalized = endpoint.removeprefix('/') if endpoint else None
    if normalized and normalized.startswith('https://api.github.com/'):
        normalized = normalized.removeprefix('https://api.github.com/')
    return normalized == ROUTE and (method or ('POST' if body else 'GET')) == 'POST'


def main():
    if blocked(sys.argv[1:]):
        # Captured privately by the old controller; never emits request data.
        print('HOUND_CI_DRAIN_NEW_JIT_BLOCKED', file=sys.stderr)
        raise SystemExit(75)
    os.environ.setdefault('GH_TELEMETRY', 'false')
    os.execv(ORIGINAL, [OLD_GH, *sys.argv[1:]])


if __name__ == '__main__':
    main()
