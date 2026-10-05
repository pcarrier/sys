#!/usr/bin/env python3
"""Host-free waiter regressions: never execute operator code or probe the host."""
import copy
from contextlib import contextmanager, ExitStack
import errno
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import select
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


waiter = ModuleType('waiter')
waiter.__file__ = str(Path(__file__).with_name('wait-drained.py'))
exec(compile(Path(waiter.__file__).read_bytes(), waiter.__file__, 'exec'), waiter.__dict__)


def source_meta(data, **changes):
    return SimpleNamespace(**{
        **dict(st_mode=0o100444, st_uid=0, st_gid=0, st_size=len(data),
               st_dev=1, st_ino=22, st_mtime_ns=33, st_ctime_ns=44), **changes,
    })


@contextmanager
def source_file(path, data, before=None, after=None):
    """One in-memory source FD; no Nix-store or host state access."""
    before = before or source_meta(data)
    with patch.object(Path, 'resolve', return_value=path), patch.object(waiter.os, 'open', return_value=91) as opened, patch.object(waiter.os, 'fstat', side_effect=[before, after or before]), patch.object(waiter.os, 'fdopen', return_value=io.BytesIO(data)), patch.object(waiter.os, 'close') as closed:
        yield opened, closed


BOOT = '1b0a7d1e-0000-4000-8000-00000000b007'
BOOT_HEX = BOOT.replace('-', '')
OTHER_BOOT_HEX = 'b' * 32


def invocation(slot):
    return f'{slot:x}' * 32


NEW_INVOCATION = 'e' * 32


def entry(slot=1):
    return {'slot': slot, 'pid': 100 + slot, 'starttime': str(1000 + slot),
            'control_group': f'/hound.slice/hound-ci.slice/hound-ci-{slot}.service',
            'qemu_uid': 980 + slot, 'qemu_gid': 970 + slot,
            'invocation_id': invocation(slot), 'boot_id': BOOT}


def manifest():
    return {
        'phase': 'armed-awaiting-job-completion', 'boot_id': BOOT,
        'created_utc': '2026-10-05T12:20:00+00:00',
        'witness_since': waiter.WITNESS_SINCE,
        'drain_nonce': '123456781234423482341234567890ab',
        'operator_source': '/nix/store/reviewed-operator.py', 'operator_sha256': 'a' * 64,
        'waiter_source': '/nix/store/reviewed-waiter.py', 'waiter_sha256': 'b' * 64,
        'validator_source': '/nix/store/reviewed-validator.py', 'validator_sha256': 'c' * 64,
        'gate': '/nix/store/reviewed-gate.py', 'gate_sha256': 'd' * 64,
        'old_source': waiter.OLD_SOURCE, 'old_source_sha256': waiter.OLD_SOURCE_SHA256,
        'controllers': [entry(slot) for slot in range(1, 5)],
        'armed': [{'slot': slot} for slot in range(1, 5)],
        'gates': {str(slot): {'stage': 'armed'} for slot in range(1, 5)},
    }


def values(e=None, main='0', group=None):
    e = e or entry()
    return {'MainPID': main, 'Restart': 'no', 'ControlGroup': e['control_group'] if group is None else group}


def controller_row(e=None, message=None, clock=10):
    e = e or entry()
    return {'_PID': str(e['pid']), '_UID': '0', '_SYSTEMD_UNIT': waiter.unit(e),
            '_BOOT_ID': BOOT_HEX, '_SYSTEMD_INVOCATION_ID': e['invocation_id'],
            '__CURSOR': f'cursor-{e["slot"]}-{clock}', '__MONOTONIC_TIMESTAMP': str(20000000 + clock),
            '__REALTIME_TIMESTAMP': str(1791201600000000 + clock),
            'MESSAGE': message or f'HOUND_CI slot={e["slot"]} VM stopped; preflight=True; runner completed=True; erasing disk'}


def start_row(e=None, clock=1):
    e = e or entry()
    return controller_row(e, f'HOUND_CI slot={e["slot"]} name=hound-ci-{e["slot"]}-{clock:012x} repo=xmit-dev/ultimator disposable VM started', clock)


def manager_row(e=None, clock=30):
    e = e or entry()
    return {'_PID': '1', '_UID': '0', '_COMM': 'systemd', 'UNIT': waiter.unit(e),
            '_BOOT_ID': BOOT_HEX, 'INVOCATION_ID': e['invocation_id'],
            '__CURSOR': f'manager-{e["slot"]}-{clock}', '__MONOTONIC_TIMESTAMP': str(20000000 + clock),
            '__REALTIME_TIMESTAMP': str(1791201600000000 + clock),
            'MESSAGE_ID': '9d1aaa27d60140bd96365438aad20286',
            'MESSAGE': 'Human-readable text is not the terminal evidence'}


def security_row(e=None, clock=2, pid=None):
    e = e or entry()
    return controller_row(e, f'HOUND_CI QEMU_SECURITY_VERIFIED pid={pid or 400 + e["slot"]} uid={e["qemu_uid"]} gid={e["qemu_gid"]} CapInh/Prm/Eff/Bnd/Amb=0 NNP=1', clock)


def lifecycle(e, item, start=1, security=2, stop=10, preflight=True, completed=True):
    waiter.consume_journal(e, item, start_row(e, start))
    waiter.consume_journal(e, item, security_row(e, security))
    waiter.consume_journal(e, item, controller_row(e, f'HOUND_CI slot={e["slot"]} VM stopped; preflight={preflight}; runner completed={completed}; erasing disk', stop))


def ready(e=None):
    e = e or entry()
    item = waiter.new_witness(e)
    lifecycle(e, item)
    waiter.consume_journal(e, item, manager_row(e))
    item['controller_exited'] = True
    return item


def registration(e=None, runner_id=None):
    e = e or entry()
    return {'repo': waiter.REPO, 'id': runner_id, 'name': f'hound-ci-{e["slot"]}-123456abcdef'}


def receipt(e=None):
    e = e or entry()
    return {'slot': e['slot'], 'old_pid': e['pid'], 'starttime': e['starttime'],
            'repo': waiter.REPO, 'name': registration(e)['name'], 'route_blocked': True,
            'utc': '2026-10-05T12:21:00+00:00'}


class PureWitnessTests(unittest.TestCase):
    def test_exact_four_armed_source_bound_manifest(self):
        self.assertEqual(waiter.validate_manifest(manifest()), [entry(slot) for slot in range(1, 5)])
        for alter in (
            lambda m: m.update(phase='pinning'),
            lambda m: m.pop('operator_sha256'),
            lambda m: m.pop('drain_nonce'),
            lambda m: m.update(drain_nonce='not-a-uuid'),
            lambda m: m.pop('waiter_sha256'),
            lambda m: m.update(waiter_source='/tmp/unreviewed.py'),
            lambda m: m.update(validator_sha256='fuzzy'),
            lambda m: m.update(old_source='/nix/store/different-supervisor.py'),
            lambda m: m.update(old_source_sha256='f' * 64),
            lambda m: m['controllers'][0].pop('qemu_uid'),
            lambda m: m['controllers'][0].update(qemu_gid=True),
            lambda m: m.pop('witness_since'),
            lambda m: m.update(witness_since=m['created_utc']),
            lambda m: m.update(created_utc='2026-10-05T11:55:00+00:00'),
            lambda m: m['controllers'].pop(),
            lambda m: m['controllers'][3].update(slot=3),
            lambda m: m['controllers'][0].update(pid=m['controllers'][1]['pid']),
            lambda m: m['controllers'][0].update(starttime='unknown'),
            lambda m: m['controllers'][0].update(control_group='/other/service'),
            lambda m: m['armed'].pop(),
            lambda m: m['armed'][0].update(slot=True),
            lambda m: m['gates']['1'].update(stage='bind-intent'),
        ):
            m = manifest(); alter(m)
            with self.assertRaises(RuntimeError):
                waiter.validate_manifest(m)

    def test_source_must_be_current_canonical_immutable_root_owned_and_hashed(self):
        path = Path('/nix/store/reviewed-operator.py')
        data = b'# data-only mock operator\n'
        sha = hashlib.sha256(data).hexdigest()
        with source_file(path, data) as (opened, closed):
            self.assertIs(waiter.validate_operator_source(path, sha), data)
        opened.assert_called_once_with(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        closed.assert_called_once_with(91)
        with source_file(path, data), self.assertRaisesRegex(RuntimeError, 'differs'):
            waiter.validate_operator_source(path, '0' * 64)
        for mode, uid, gid in ((0o100644, 0, 0), (0o100444, 1000, 0),
                               (0o100444, 0, 1000), (0o040555, 0, 0)):
            with source_file(path, data, source_meta(data, st_mode=mode, st_uid=uid, st_gid=gid)), self.assertRaises(RuntimeError):
                waiter.validate_operator_source(path, sha)
        with patch.object(Path, 'resolve', return_value=Path('/nix/store/other.py')), patch.object(waiter.os, 'open') as opened, self.assertRaises(RuntimeError):
            waiter.validate_operator_source(path, sha)
        opened.assert_not_called()
        with self.assertRaises(RuntimeError):
            waiter.validate_operator_source(Path('/tmp/operator.py'), sha)

    def test_source_sha_syntax_and_source_size_bound_are_checked_before_read(self):
        path = Path('/nix/store/reviewed-operator.py'); data = b'# fixture\n'
        for sha in (None, 'fuzzy', 'A' * 64, 'a' * 63):
            with patch.object(Path, 'resolve', return_value=path), patch.object(waiter.os, 'open') as opened, self.assertRaisesRegex(RuntimeError, 'SHA pin'):
                waiter.validate_operator_source(path, sha)
            opened.assert_not_called()
        for size in (0, waiter.MAX_SOURCE + 1):
            with source_file(path, data, source_meta(data, st_size=size)) as (_, closed), patch.object(waiter.os, 'fdopen') as stream, self.assertRaisesRegex(RuntimeError, 'bound'):
                waiter.validate_operator_source(path, hashlib.sha256(data).hexdigest())
            stream.assert_not_called(); closed.assert_called_once_with(91)

    def test_source_changed_truncated_or_overgrown_during_read_fails_closed(self):
        path = Path('/nix/store/reviewed-operator.py'); data = b'# fixture\n'
        sha = hashlib.sha256(data).hexdigest()
        for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
            before = source_meta(data); after = source_meta(data, **{key: getattr(before, key) + 1})
            with self.subTest(key=key), source_file(path, data, before, after), self.assertRaisesRegex(RuntimeError, 'changed during read'):
                waiter.validate_operator_source(path, sha)
        for supplied in (data[:-1], data + b'x', b'x' * (waiter.MAX_SOURCE + 1)):
            with source_file(path, supplied, source_meta(data)), self.assertRaisesRegex(RuntimeError, 'changed during read'):
                waiter.validate_operator_source(path, sha)

    def test_symlink_swap_after_canonical_check_is_not_followed(self):
        path = Path('/nix/store/reviewed-operator.py')
        with patch.object(Path, 'resolve', return_value=path), patch.object(waiter.os, 'open', side_effect=OSError(errno.ELOOP, 'mock replaced by symlink')) as opened, patch.object(waiter.os, 'fdopen') as read, self.assertRaises(OSError):
            waiter.validate_operator_source(path, 'a' * 64)
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        read.assert_not_called()

    def test_subtree_populated_not_direct_procs(self):
        # Direct cgroup.procs could be empty here while a child cgroup is busy.
        self.assertTrue(waiter.populated('populated 1\nfrozen 0\n'))
        self.assertFalse(waiter.evaluate(entry(), ready(), values(), True, None))
        self.assertFalse(waiter.populated('populated 0\nfrozen 0\n'))
        self.assertTrue(waiter.evaluate(entry(), ready(), values(), False, None))
        for text in ('', 'frozen 0\n', 'populated unknown\n', 'populated 2\n', 'populated 0\npopulated 1\n', 'populated\n'):
            with self.assertRaises(RuntimeError):
                waiter.populated(text)

    def test_populated_pollerr_is_a_modification_not_a_dead_watch(self):
        self.assertFalse(waiter.cgroup_watch_removed(select.POLLPRI | select.POLLERR))
        self.assertFalse(waiter.cgroup_watch_removed(select.POLLHUP))
        self.assertTrue(waiter.cgroup_watch_removed(select.POLLERR, OSError(errno.ENODEV, 'mock removed')))
        self.assertTrue(waiter.cgroup_watch_removed(select.POLLERR, OSError(errno.ENOENT, 'mock removed')))
        with self.assertRaises(OSError):
            waiter.cgroup_watch_removed(select.POLLERR, OSError(errno.EACCES, 'mock denied'))
        with self.assertRaises(RuntimeError):
            waiter.cgroup_watch_removed(select.POLLNVAL)

    def test_removed_group_requires_identity_bound_exit_zero_and_manager(self):
        self.assertTrue(waiter.evaluate(entry(), ready(), values(group=''), None, None))
        self.assertTrue(waiter.evaluate(entry(), ready(), values(), None, None))
        item = ready(); item['controller_exited'] = False
        self.assertFalse(waiter.evaluate(entry(), item, values(group=''), None, None))
        item = ready(); item['manager_terminal'] = None
        self.assertFalse(waiter.evaluate(entry(), item, values(group=''), None, None))
        self.assertFalse(waiter.evaluate(entry(), ready(), values(main=str(entry()['pid'])), None, None))
        for current in (values(group='/other/service'), values(main='999'), {**values(), 'Restart': 'always'}):
            with self.assertRaises(RuntimeError):
                waiter.evaluate(entry(), ready(), current, None, None)
        with self.assertRaises(RuntimeError):
            waiter.evaluate(entry(), ready(), values(main=str(entry()['pid']), group=''), False, None)

    def test_null_intent_without_exact_positive_receipt_is_uncertain(self):
        self.assertEqual(waiter.registration_witness(entry(), None), {'kind': 'public-record-absent'})
        with self.assertRaisesRegex(RuntimeError, 'Uncertain public registration'):
            waiter.registration_witness(entry(), registration())
        with self.assertRaisesRegex(RuntimeError, 'Positive old registration'):
            waiter.registration_witness(entry(), registration(runner_id=123), receipt())
        self.assertEqual(waiter.registration_witness(entry(), registration(), receipt())['kind'], 'exact-local-post-blocked')

    def test_receipt_is_exact_name_old_pid_starttime_repo_and_positive_boolean(self):
        for key, value in (('slot', 2), ('old_pid', 999), ('starttime', 'reused'),
                           ('repo', 'other/repo'), ('name', 'hound-ci-1-aaaaaaaaaaaa'),
                           ('route_blocked', False), ('route_blocked', 1),
                           ('utc', '2026-10-05T11:55:00+00:00'), ('utc', '2026-10-05T12:21:00')):
            changed = {**receipt(), key: value}
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                waiter.registration_witness(entry(), registration(), changed)
        with self.assertRaises(RuntimeError):
            waiter.registration_witness(entry(), registration(), {**receipt(), 'argv': 'never accepted'})
        with self.assertRaises(RuntimeError):
            waiter.registration_witness(entry(), {**registration(), 'name': 'fuzzy-prefix'}, receipt())

    def test_receipt_file_is_bounded_root_owned_0600_regular_nofollow(self):
        payload = json.dumps(receipt()).encode()
        path = Path('/data-only-mock/blocked-jit-1.json')
        meta = SimpleNamespace(st_mode=0o100600, st_uid=0, st_size=len(payload))
        with patch.object(waiter.os, 'open', return_value=91) as opened, patch.object(waiter.os, 'fstat', return_value=meta), patch.object(waiter.os, 'fdopen', return_value=io.BytesIO(payload)), patch.object(waiter.os, 'close'):
            self.assertEqual(waiter.read_public_json(path, 4096), receipt())
            self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        for mode, uid, size in ((0o100644, 0, len(payload)), (0o100600, 1000, len(payload)),
                                (0o040600, 0, len(payload)), (0o100600, 0, 4097)):
            meta = SimpleNamespace(st_mode=mode, st_uid=uid, st_size=size)
            with patch.object(waiter.os, 'open', return_value=91), patch.object(waiter.os, 'fstat', return_value=meta), patch.object(waiter.os, 'close'):
                with self.assertRaises(RuntimeError):
                    waiter.read_public_json(path, 4096)
        meta = SimpleNamespace(st_mode=0o100600, st_uid=0, st_size=1)
        with patch.object(waiter.os, 'open', return_value=91), patch.object(waiter.os, 'fstat', return_value=meta), patch.object(waiter.os, 'fdopen', return_value=io.BytesIO(b'x' * 4097)), patch.object(waiter.os, 'close'):
            with self.assertRaises(RuntimeError):
                waiter.read_public_json(path, 4096)

    def test_gate_blocked_post_job_idle_window_completed_before_gate_created(self):
        m = manifest()
        item = waiter.new_witness(entry())
        row = controller_row(); row['__REALTIME_TIMESTAMP'] = '1791201900000000'
        waiter.consume_journal(entry(), item, start_row())
        waiter.consume_journal(entry(), item, security_row())
        waiter.consume_journal(entry(), item, row)  # Real stop before 12:20 arming.
        waiter.consume_journal(entry(), item, manager_row())
        item['controller_exited'] = True
        self.assertLess('2026-10-05T12:05:00+00:00', m['created_utc'])
        self.assertTrue(waiter.evaluate(entry(), item, values(), False, registration(), receipt()))
        self.assertTrue(item['completed_vm'])

    def test_new_vm_start_revokes_prior_completion_even_if_old_job_passed(self):
        item = ready()
        self.assertTrue(waiter.evaluate(entry(), item, values(), False, None))
        waiter.consume_journal(entry(), item, start_row(clock=40))
        self.assertFalse(item['completed_vm']); self.assertFalse(item['drained'])
        self.assertFalse(waiter.evaluate(entry(), item, values(), False, None))
        # A replay/follow overlap must not resurrect the earlier successful job.
        self.assertFalse(waiter.consume_journal(entry(), item, controller_row(clock=10)))
        self.assertFalse(item['completed_vm'])

    def test_guest_preflight_and_completed_flags_are_advisory_not_hardware_gate(self):
        for preflight, completed in ((True, False), (False, True), (False, False), (True, True)):
            with self.subTest(preflight=preflight, completed=completed):
                item = ready()
                lifecycle(entry(), item, start=40, security=45, stop=50, preflight=preflight, completed=completed)
                self.assertEqual(item['completed_vm'], preflight and completed)
                # Old manager terminal precedes B's STOP: cannot certify hardware.
                self.assertFalse(waiter.evaluate(entry(), item, values(), False, None))
                waiter.consume_journal(entry(), item, manager_row(clock=60))
                self.assertTrue(waiter.evaluate(entry(), item, values(), False, None))
                self.assertFalse(item['job_certified'])

    def test_manager_stream_arrival_order_does_not_override_clock_order(self):
        for stop, accepted in ((20, True), (40, False)):
            item = waiter.new_witness(entry()); item['controller_exited'] = True
            waiter.consume_journal(entry(), item, start_row())
            waiter.consume_journal(entry(), item, security_row())
            waiter.consume_journal(entry(), item, manager_row(clock=30))
            self.assertFalse(waiter.evaluate(entry(), item, values(group=''), None, None))
            waiter.consume_journal(entry(), item, controller_row(clock=stop))
            self.assertEqual(waiter.evaluate(entry(), item, values(group=''), None, None), accepted)
            if not accepted:
                waiter.consume_journal(entry(), item, manager_row(clock=50))
                self.assertTrue(waiter.evaluate(entry(), item, values(group=''), None, None))

    def test_zero_work_or_malformed_stop_never_assumed_completed(self):
        item = waiter.new_witness(entry()); item['controller_exited'] = True
        waiter.consume_journal(entry(), item, manager_row())
        for message in ('HOUND_CI slot=1 VM stopped; preflight=1; runner completed=1; erasing disk',
                        'HOUND_CI slot=1 VM stopped; preflight=True; runner completed=True; erasing disk extra',
                        'controller failed: CalledProcessError', 'preflight=True; runner completed=True; erasing disk'):
            self.assertFalse(waiter.consume_journal(entry(), item, controller_row(message=message)))
        self.assertFalse(waiter.evaluate(entry(), item, values(group=''), None, None))

    def test_manager_message_must_be_exact_unit_trusted_pid1_root_comm_and_typed(self):
        for key, value in (('_PID', '2'), ('_UID', '1000'), ('_COMM', 'not-systemd'),
                           ('UNIT', 'hound-ci-2.service'), ('MESSAGE_ID', 'untyped')):
            row = {**manager_row(), key: value}
            self.assertIsNone(waiter.journal_event(entry(), row))
        for message_id in waiter.MANAGER_TERMINAL_IDS:
            self.assertEqual(waiter.journal_event(entry(), {**manager_row(), 'MESSAGE_ID': message_id})['kind'], 'manager-terminal')
        row = manager_row(); row.pop('UNIT'); row['_SYSTEMD_UNIT'] = waiter.unit(entry())
        self.assertIsNone(waiter.journal_event(entry(), row))

    def test_manifest_requires_canonical_boot_and_distinct_exact_invocations(self):
        self.assertEqual(waiter.validate_manifest(manifest())[0]['invocation_id'], invocation(1))
        for change in (lambda m: m.pop('boot_id'), lambda m: m.update(boot_id=BOOT_HEX), lambda m: m.update(boot_id=BOOT.upper()),
                       lambda m: m['controllers'][0].pop('invocation_id'), lambda m: m['controllers'][0].update(invocation_id='A' * 32),
                       lambda m: m['controllers'][0].update(invocation_id=invocation(2)),
                       lambda m: m['controllers'][0].update(boot_id='2c0a7d1e-0000-4000-8000-00000000b007'),
                       lambda m: m['controllers'][0].pop('boot_id')):
            m = manifest(); change(m)
            with self.assertRaises(RuntimeError): waiter.validate_manifest(m)
        self.assertIn('_BOOT_ID', waiter.JOURNAL_FIELDS.split(','))
        self.assertIn('INVOCATION_ID', waiter.JOURNAL_FIELDS.split(','))
        self.assertIn('_SYSTEMD_INVOCATION_ID', waiter.JOURNAL_FIELDS.split(','))

    def test_manager_terminal_requires_exact_original_invocation_never_UNIT_alone(self):
        e = entry()
        self.assertEqual(waiter.journal_event(e, manager_row(e))['kind'], 'manager-terminal')
        # A NEW invocation of the same unit (activation's tracked start, or any
        # later start) is not the original slot's terminal evidence.
        self.assertIsNone(waiter.journal_event(e, {**manager_row(e), 'INVOCATION_ID': NEW_INVOCATION}))
        self.assertIsNone(waiter.journal_event(e, {**manager_row(e), 'INVOCATION_ID': invocation(2)}))
        # UNIT alone (no/invalid invocation) or another boot is HOLD, never accepted.
        for change in ({'INVOCATION_ID': None}, {'INVOCATION_ID': 'E' * 32}, {'INVOCATION_ID': [e['invocation_id']] * 2},
                       {'_BOOT_ID': OTHER_BOOT_HEX}, {'_BOOT_ID': BOOT}, {'_BOOT_ID': None}):
            row = {**manager_row(e), **change}
            row = {key: value for key, value in row.items() if value is not None}
            with self.subTest(change=change), self.assertRaises(RuntimeError): waiter.journal_event(e, row)
        # Non-terminal manager rows are never interpreted, whatever invocation.
        self.assertIsNone(waiter.journal_event(e, {**manager_row(e), 'MESSAGE_ID': '39f53479d3a045ac8e11786248231fbf', 'INVOCATION_ID': NEW_INVOCATION}))

    def test_controller_rows_require_original_boot_and_systemd_invocation(self):
        e = entry()
        self.assertEqual(waiter.journal_event(e, start_row(e))['kind'], 'started')
        self.assertIsNone(waiter.journal_event(e, {**start_row(e), '_SYSTEMD_INVOCATION_ID': NEW_INVOCATION}))
        for change in ({'_SYSTEMD_INVOCATION_ID': None}, {'_SYSTEMD_INVOCATION_ID': 'x' * 32}, {'_BOOT_ID': OTHER_BOOT_HEX}, {'_BOOT_ID': None}):
            row = {key: value for key, value in {**start_row(e), **change}.items() if value is not None}
            with self.subTest(change=change), self.assertRaises(RuntimeError): waiter.journal_event(e, row)

    def test_later_new_invocation_events_never_revoke_or_satisfy_original_proof(self):
        e = entry(); item = ready(e)
        before = copy.deepcopy(item)
        self.assertTrue(waiter.hardware_lifecycle_ready(e, item))
        later = [{**start_row(e, 50), '_SYSTEMD_INVOCATION_ID': NEW_INVOCATION},
                 {**security_row(e, 51), '_SYSTEMD_INVOCATION_ID': NEW_INVOCATION},
                 {**manager_row(e, 60), 'INVOCATION_ID': NEW_INVOCATION, 'MESSAGE_ID': sorted(waiter.MANAGER_TERMINAL_IDS)[1]}]
        for row in later:
            self.assertFalse(waiter.consume_journal(e, item, row))
        self.assertEqual(item, before)
        # Conversely: new-invocation terminal/lifecycle records alone never
        # complete an original slot that still lacks its own manager terminal.
        fresh = waiter.new_witness(e); lifecycle(e, fresh)
        self.assertFalse(waiter.consume_journal(e, fresh, {**manager_row(e), 'INVOCATION_ID': NEW_INVOCATION}))
        self.assertIsNone(fresh['manager_terminal'])
        # But a later START by the ORIGINAL invocation still revokes completion.
        self.assertTrue(waiter.consume_journal(e, item, start_row(e, 70)))
        self.assertFalse(waiter.hardware_lifecycle_ready(e, item))

    def test_controller_vm_messages_bound_to_old_pid_and_exact_unit(self):
        for row in ({**controller_row(), '_PID': '999'},
                    {**controller_row(), '_UID': '1000'},
                    {**controller_row(), '_SYSTEMD_UNIT': 'hound-ci-2.service'},
                    controller_row(message='HOUND_CI slot=2 VM stopped; preflight=True; runner completed=True; erasing disk')):
            self.assertIsNone(waiter.journal_event(entry(), row))
        row = controller_row(); row.pop('__MONOTONIC_TIMESTAMP')
        with self.assertRaises(RuntimeError):
            waiter.consume_journal(entry(), waiter.new_witness(entry()), row)

    def test_journal_replays_full_boot_without_since_and_cursor_follows(self):
        argv = waiter.journal_command(manifest())
        self.assertNotIn(waiter.WITNESS_SINCE, argv)
        self.assertNotIn('--since', argv)
        self.assertNotIn(manifest()['created_utc'], argv)
        self.assertIn('--no-tail', argv); self.assertIn('--boot', argv)
        follow = waiter.journal_command(manifest(), follow=True, cursor='exact-cursor')
        self.assertIn('--after-cursor', follow); self.assertIn('exact-cursor', follow)
        self.assertNotIn('--since', follow); self.assertIn('--follow', follow)
        self.assertNotIn('_CMDLINE', waiter.JOURNAL_FIELDS)
        for slot in range(1, 5):
            self.assertIn(f'hound-ci-{slot}.service', argv)

    def test_no_timers_api_signals_credentials_or_direct_procs_fallback(self):
        source = Path(__file__).with_name('wait-drained.py').read_text()
        for forbidden in ('time.sleep(', 'poller.poll(1000', 'os.kill(', '/environ', 'hosts.yml',
                          'seed.iso', 'console.log', 'CREDENTIALS_DIRECTORY', "['gh'", "['systemctl', 'stop'", "['systemctl', 'start'"):
            self.assertNotIn(forbidden, source)
        self.assertIn('poller.poll()', source)
        self.assertNotIn("/ 'cgroup.procs'", source)

    def test_all_sources_including_own_waiter_are_authenticated_before_import(self):
        m = manifest()
        with patch.object(waiter, 'validate_operator_source', return_value=b'# authenticated source\n') as validate:
            authenticated = waiter.validate_sources(m, Path(m['operator_source']), Path(m['waiter_source']))
        self.assertEqual(authenticated, {path: b'# authenticated source\n' for path, _ in waiter.SOURCE_FIELDS})
        self.assertEqual(validate.call_args_list, [unittest.mock.call(Path(m[path]), m[sha]) for path, sha in waiter.SOURCE_FIELDS])
        for operator, own in ((Path('/tmp/operator.py'), Path(m['waiter_source'])),
                              (Path(m['operator_source']), Path('/tmp/waiter.py'))):
            with patch.object(waiter, 'validate_operator_source') as validate, self.assertRaisesRegex(RuntimeError, 'Execution source'):
                waiter.validate_sources(m, operator, own)
            validate.assert_not_called()
        with patch.object(waiter, 'read_public_json', return_value=m), patch.object(waiter, '__file__', m['waiter_source']), patch.object(waiter, 'validate_operator_source', side_effect=RuntimeError('Wrong reviewed source hash')), patch.object(waiter, 'load_operator') as load, patch.object(waiter, 'wait_drained') as run, patch('sys.argv', ['wait-drained.py', '--operator-source', m['operator_source']]):
            with self.assertRaisesRegex(RuntimeError, 'Wrong reviewed source hash'):
                waiter.main()
        load.assert_not_called(); run.assert_not_called()
        # Specifically exercise the manifest-pinned own waiter hash, rather
        # than accepting a valid operator hash and trusting arbitrary waiter.
        def fail_own(path, sha):
            if path == Path(m['waiter_source']):
                raise RuntimeError('Own waiter hash differs')
        with patch.object(waiter, 'validate_operator_source', side_effect=fail_own), self.assertRaisesRegex(RuntimeError, 'Own waiter'):
            waiter.validate_sources(m, Path(m['operator_source']), Path(m['waiter_source']))

    def test_finisher_seed_starts_empty_and_does_not_trust_cached_witness(self):
        m = manifest(); e = entry()
        m['phase'] = waiter.HARDWARE_PHASE
        m['drain_witness'] = {'1': ready(e)}
        seeded = waiter.seed_witness(e, m)
        self.assertEqual(seeded, waiter.new_witness(e))
        self.assertFalse(seeded['controller_exited'])
        self.assertFalse(seeded['completed_vm'])
        self.assertFalse(seeded['job_certified'])
        self.assertEqual(seeded['vm_history'], [])
        self.assertIsNone(seeded['manager_terminal'])
        with self.assertRaisesRegex(RuntimeError, 'exact manifest controller'):
            waiter.seed_witness({**e, 'pid': 999}, m)

    def test_prior_pid_reuse_lifecycle_before_starttime_is_ignored(self):
        e = entry(); item = waiter.new_witness(e)
        self.assertTrue(waiter.controller_started_before(e, '10010000', ticks=100))
        self.assertFalse(waiter.controller_started_before(e, '10009999', ticks=100))
        with self.assertRaises(RuntimeError):
            waiter.controller_started_before(e, '10010000', ticks=0)
        with patch.object(waiter.os, 'sysconf', return_value=100):
            for row in (start_row(e), security_row(e), controller_row(e), manager_row(e)):
                row['__MONOTONIC_TIMESTAMP'] = '10009999'
                self.assertFalse(waiter.consume_journal(e, item, row))
        self.assertEqual(item['vm_history'], [])
        self.assertIsNone(item['manager_terminal'])

    def test_prior_reused_pid_wrong_slot_security_is_filtered_before_validation(self):
        e = entry(); item = waiter.new_witness(e)
        row = security_row(e)
        row['MESSAGE'] = row['MESSAGE'].replace(f'uid={e["qemu_uid"]}', 'uid=9999')
        row['__MONOTONIC_TIMESTAMP'] = '1'
        self.assertFalse(waiter.consume_journal(e, item, row))
        self.assertEqual(item['vm_history'], [])
        item = ready(e); item['job_certified'] = True
        self.assertTrue(waiter.evaluate(e, item, values(), False, None))
        self.assertIs(item['job_certified'], False)

    def test_missing_start_or_security_and_startup_never_become_hardware_drained(self):
        e = entry()
        for row in (security_row(e), controller_row(e)):
            with self.assertRaisesRegex(RuntimeError, 'START'):
                waiter.consume_journal(e, waiter.new_witness(e), row)
        for stopped in (False, True):
            item = waiter.new_witness(e); item['controller_exited'] = True
            waiter.consume_journal(e, item, start_row(e))
            if stopped:
                waiter.consume_journal(e, item, controller_row(e))
                self.assertTrue(item['completed_vm'])  # Still NOT hardware proof.
            waiter.consume_journal(e, item, manager_row(e))
            self.assertFalse(waiter.evaluate(e, item, values(), False, None))
            self.assertFalse(item['job_certified'])
        item = ready()
        waiter.consume_journal(e, item, start_row(e, 40))
        waiter.consume_journal(e, item, security_row(e, 45))
        waiter.consume_journal(e, item, manager_row(e, 60))
        self.assertFalse(waiter.evaluate(e, item, values(), False, None))
        self.assertFalse(item['completed_vm'])

    def test_forged_guest_serial_finishes_and_root_looking_lifecycle_are_not_trusted(self):
        e = entry(); item = waiter.new_witness(e)
        messages = (start_row(e)['MESSAGE'], security_row(e)['MESSAGE'],
                    controller_row(e)['MESSAGE'], 'HOUND_CI_RUNNER_FINISHED',
                    'HOUND_CI_GUEST_PREFLIGHT_OK', 'HOUND_CI slot=1 HOUND_CI_RUNNER_FINISHED')
        for message in messages:
            for uid, pid in (('0', str(401)), (str(e['qemu_uid']), str(e['pid'])),
                             (str(e['qemu_uid']), str(401))):
                row = {**controller_row(e, message), '_UID': uid, '_PID': pid}
                self.assertFalse(waiter.consume_journal(e, item, row))
        self.assertFalse(waiter.consume_journal(e, item, controller_row(e, 'HOUND_CI_RUNNER_FINISHED')))
        self.assertEqual(item['vm_history'], [])
        item['controller_exited'] = True
        waiter.consume_journal(e, item, manager_row(e))
        self.assertFalse(waiter.evaluate(e, item, values(), False, None))

    def test_qemu_security_needs_root_controller_sender_actual_slot_uid_and_valid_pid(self):
        e = entry()
        for wrong in ({**security_row(e), '_PID': '401'},
                      {**security_row(e), '_UID': str(e['qemu_uid'])}):
            self.assertIsNone(waiter.journal_event(e, wrong))
        for pid in (1, e['pid']):
            with self.assertRaisesRegex(RuntimeError, 'QEMU PID'):
                waiter.journal_event(e, security_row(e, pid=pid))
        for key in ('qemu_uid', 'qemu_gid'):
            wrong = {**e, key: e[key] + 1}
            with self.assertRaisesRegex(RuntimeError, 'UID identity mismatch'):
                waiter.journal_event(wrong, security_row(e))
        item = waiter.new_witness(e)
        waiter.consume_journal(e, item, start_row(e))
        waiter.consume_journal(e, item, security_row(e, pid=401))
        self.assertEqual(item['vm_history'][-1]['qemu_pid'], 401)

    def test_same_clock_distinct_events_survive_but_cursor_overlap_is_deduped(self):
        e = entry(); item = waiter.new_witness(e)
        rows = [start_row(e, 10), security_row(e, 10), controller_row(e, clock=10)]
        for i, row in enumerate(rows):
            row['__CURSOR'] = f'root-same-clock-{i}'
            self.assertTrue(waiter.consume_journal(e, item, row))
        for row in rows:
            self.assertFalse(waiter.consume_journal(e, item, row))
        self.assertEqual(len(item['vm_history']), 1)
        self.assertTrue(item['vm_history'][0]['security_verified'])
        item['controller_exited'] = True
        waiter.consume_journal(e, item, manager_row(e, 10))
        self.assertTrue(waiter.evaluate(e, item, values(), False, None))
        changed = {**rows[-1], 'MESSAGE': 'HOUND_CI slot=1 VM stopped; preflight=False; runner completed=False; erasing disk'}
        with self.assertRaisesRegex(RuntimeError, 'cursor changed'):
            waiter.consume_journal(e, item, changed)

    def test_full_root_history_preserves_preapproval_vm_times_and_is_bounded(self):
        e = entry(); item = waiter.new_witness(e)
        for i in range(waiter.MAX_VM_HISTORY):
            lifecycle(e, item, start=3 * i + 1, security=3 * i + 2, stop=3 * i + 3)
        self.assertEqual(len(item['vm_history']), waiter.MAX_VM_HISTORY)
        first = item['vm_history'][0]
        self.assertEqual(first['start_realtime'], '1791201600000001')
        self.assertEqual(first['stop_realtime'], '1791201600000003')
        self.assertEqual(first['start_monotonic'], '20000001')
        self.assertEqual(first['stop_monotonic'], '20000003')
        with self.assertRaisesRegex(RuntimeError, 'history bound'):
            waiter.consume_journal(e, item, start_row(e, 1000))

    def test_latest_hardware_record_requires_exact_name_and_ordered_root_clocks(self):
        for mutate in (lambda i: i['latest_vm'].update(name='hound-ci-1-ffffffffffff'),
                       lambda i: i['vm_history'][-1].update(security_verified=False),
                       lambda i: i['vm_history'][-1].update(qemu_pid=1),
                       lambda i: i['vm_history'][-1].update(start_realtime=None),
                       lambda i: i['vm_history'][-1].update(start_monotonic='20000099'),
                       lambda i: i.update(latest_vm_monotonic='20000009'),
                       lambda i: i.update(manager_monotonic='20000009')):
            item = ready(); mutate(item)
            self.assertFalse(waiter.evaluate(entry(), item, values(), False, None))


class SourceLoaderTests(unittest.TestCase):
    def fixture(self, state=None):
        m = manifest()
        operator = ("from pathlib import Path\n"
                    f"STATE = Path({str(state or waiter.STATE)!r})\n"
                    "MODULE_PROBE = 'authenticated-source'\n"
                    "SOURCE_NAME = __name__\nSOURCE_FILE = __file__\n"
                    "if __name__ == '__main__':\n"
                    "    raise RuntimeError('armer CLI must not run during module loading')\n").encode()
        sources = {m[path]: operator if path == 'operator_source' else f'# authenticated {path}\n'.encode()
                   for path, _ in waiter.SOURCE_FIELDS}
        for path, sha in waiter.SOURCE_FIELDS:
            m[sha] = hashlib.sha256(sources[m[path]]).hexdigest()
        return m, sources

    @contextmanager
    def cli(self, m, sources, second_manifest=None):
        # ALL five source authentications use the real canonical/no-follow/hash
        # code on in-memory FDs; only data-only public manifest reads are mocked.
        fd_paths = {}
        def open_source(path, flags):
            self.assertEqual(flags, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            fd = 91 + len(fd_paths); fd_paths[fd] = str(path)
            return fd
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, 'resolve', autospec=True, side_effect=lambda path, strict=True: path))
            opened = stack.enter_context(patch.object(waiter.os, 'open', side_effect=open_source))
            stack.enter_context(patch.object(waiter.os, 'fstat', side_effect=lambda fd: source_meta(sources[fd_paths[fd]])))
            stack.enter_context(patch.object(waiter.os, 'fdopen', side_effect=lambda fd, *args, **kwargs: io.BytesIO(sources[fd_paths[fd]])))
            closed = stack.enter_context(patch.object(waiter.os, 'close'))
            stack.enter_context(patch.object(waiter, 'read_public_json', side_effect=[m, second_manifest if second_manifest is not None else m]))
            stack.enter_context(patch.object(waiter, '__file__', m['waiter_source']))
            stack.enter_context(patch.object(waiter, 'OLD_SOURCE_SHA256', m['old_source_sha256']))
            run = stack.enter_context(patch.object(waiter, 'wait_drained'))
            stack.enter_context(patch('sys.argv', ['wait-drained.py', '--operator-source', m['operator_source']]))
            yield run, opened, closed

    def assert_authenticated_module(self, m, run):
        drain, received_manifest = run.call_args.args
        self.assertIs(received_manifest, m)
        self.assertIsInstance(drain, ModuleType)
        self.assertEqual(drain.STATE, waiter.STATE)
        self.assertEqual(drain.MODULE_PROBE, 'authenticated-source')
        self.assertEqual(drain.__name__, 'drain')
        self.assertEqual(drain.SOURCE_NAME, 'drain')
        self.assertEqual(drain.__file__, m['operator_source'])
        self.assertEqual(drain.SOURCE_FILE, m['operator_source'])

    def test_cli_compiles_exact_authenticated_bytes_without_import_loader_or_reopening(self):
        m, sources = self.fixture(); authenticated = {}
        real_validate = waiter.validate_operator_source
        def validate(path, sha):
            data = real_validate(path, sha); authenticated[str(path)] = data
            return data
        with self.cli(m, sources) as (run, opened, closed), patch.object(waiter, 'validate_operator_source', side_effect=validate), patch('builtins.compile', wraps=compile) as compiled, patch.object(importlib.util, 'spec_from_file_location', side_effect=AssertionError('Unreviewed loader forbidden')) as spec, patch.object(importlib.machinery.SourceFileLoader, 'exec_module', side_effect=AssertionError('Cached execution forbidden')) as cached:
            waiter.main()
        spec.assert_not_called(); cached.assert_not_called()
        self.assertEqual(opened.call_args_list, [unittest.mock.call(Path(m[path]), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW) for path, _ in waiter.SOURCE_FIELDS])
        self.assertEqual(closed.call_count, 5)
        compiled.assert_called_once_with(authenticated[m['operator_source']], m['operator_source'], 'exec')
        self.assertIs(compiled.call_args.args[0], authenticated[m['operator_source']])
        self.assert_authenticated_module(m, run)

    def test_matching_existing_bytecode_cache_is_accepted_by_old_loader_not_by_cli(self):
        m, sources = self.fixture(); path = m['operator_source']; reviewed = sources[path]
        # Model a pre-existing sibling .pyc in memory, with a valid magic/header
        # and matching source timestamp/size but an UNREVIEWED code payload.
        # No cache code is executed and no real file/cache is ever created.
        poison = compile("MODULE_PROBE = 'unreviewed-cache'\n", path, 'exec')
        mtime = 1700000000
        cache = (importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, mtime, len(reviewed)) +
                 marshal.dumps(poison))
        cache_path = importlib.util.cache_from_source(path)
        loader = importlib.machinery.SourceFileLoader('drain', path)
        with patch.object(sys, 'dont_write_bytecode', True), patch.object(loader, 'path_stats', return_value={'mtime': mtime, 'size': len(reviewed)}), patch.object(loader, 'get_data', side_effect=lambda name: {path: reviewed, cache_path: cache}[name]) as get_data, patch.object(loader, 'set_data', side_effect=AssertionError('Cache writes forbidden')) as write:
            cached_code = loader.get_code('drain')
        get_data.assert_called_once_with(cache_path); write.assert_not_called()
        self.assertIn('unreviewed-cache', cached_code.co_consts)
        with self.cli(m, sources) as (run, _, _), patch.object(sys, 'dont_write_bytecode', True), patch.object(importlib.machinery.SourceFileLoader, 'get_code', side_effect=AssertionError('Even existing cache must never be read')) as cache_read, patch.object(importlib.util, 'spec_from_file_location', side_effect=AssertionError('No cache-capable source loader')) as spec:
            waiter.main()
        cache_read.assert_not_called(); spec.assert_not_called()
        self.assert_authenticated_module(m, run)

    def test_source_replacement_after_authentication_cannot_change_executed_bytes(self):
        m, sources = self.fixture(); original = sources[m['operator_source']]
        real_validate = waiter.validate_sources
        def authenticate_then_replace(*args):
            authenticated = real_validate(*args)
            sources[m['operator_source']] = b"raise RuntimeError('unreviewed source reopening')\n"
            return authenticated
        with self.cli(m, sources) as (run, opened, _), patch.object(waiter, 'validate_sources', side_effect=authenticate_then_replace), patch('builtins.compile', wraps=compile) as compiled:
            waiter.main()
        self.assertEqual(opened.call_count, 5)
        compiled.assert_called_once_with(original, m['operator_source'], 'exec')
        self.assert_authenticated_module(m, run)

    def test_every_source_hash_failure_prevents_any_operator_compilation_or_wait(self):
        for _, sha in waiter.SOURCE_FIELDS:
            with self.subTest(sha=sha):
                m, sources = self.fixture(); m[sha] = '0' * 64
                with self.cli(m, sources) as (run, _, _), patch('builtins.compile', side_effect=AssertionError('No code before all hashes accepted')) as compiled, self.assertRaisesRegex(RuntimeError, 'differs'):
                    waiter.main()
                compiled.assert_not_called(); run.assert_not_called()

    def test_loaded_state_or_reread_manifest_mismatch_prevents_wait(self):
        m, sources = self.fixture(state='/another/state')
        with self.cli(m, sources) as (run, _, _), self.assertRaisesRegex(RuntimeError, 'Armed state changed'):
            waiter.main()
        run.assert_not_called()
        m, sources = self.fixture()
        changed = {**m, 'drain_nonce': 'ffffffffffff4fff8fffffffffffffff'}
        with self.cli(m, sources, second_manifest=changed) as (run, _, _), self.assertRaisesRegex(RuntimeError, 'Armed state changed'):
            waiter.main()
        run.assert_not_called()

    def test_waiter_source_contains_no_cache_capable_execution_loader(self):
        source = Path(waiter.__file__).read_text()
        for forbidden in ('importlib', 'exec_module(', 'module_from_spec(', 'spec_from_file_location('):
            self.assertNotIn(forbidden, source)
        self.assertIn("compile(authenticated_bytes, str(source), 'exec')", source)


class MockWatchTests(unittest.TestCase):
    def watch(self):
        value = waiter.CgroupWatch.__new__(waiter.CgroupWatch)
        value.fd = 91; value.inode = 22
        value.path = Mock(); value.path.stat.return_value = SimpleNamespace(st_ino=22)
        value.group = Mock(); value.group.stat.return_value = SimpleNamespace(st_ino=23)
        return value

    def test_usable_watch_retained_after_populated0_and_populated1_pollerr(self):
        value = self.watch()
        with patch.object(waiter.os, 'lseek'), patch.object(waiter.os, 'read', side_effect=[b'populated 1\nfrozen 0\n', b'populated 0\nfrozen 0\n']), patch.object(waiter.os, 'close') as close:
            self.assertTrue(value.sample(select.POLLPRI | select.POLLERR))
            self.assertFalse(value.sample(select.POLLPRI | select.POLLERR))
            self.assertEqual(value.fd, 91); close.assert_not_called()

    def test_removed_watch_requires_actual_original_group_absence(self):
        value = self.watch()
        with patch.object(waiter.os, 'lseek'), patch.object(waiter.os, 'read', side_effect=OSError(errno.ENODEV, 'mock removed')):
            with self.assertRaises(RuntimeError):
                value.sample(select.POLLERR)
            value.group.stat.side_effect = FileNotFoundError
            self.assertIsNone(value.sample(select.POLLERR))

    def test_inaccessible_group_is_unknown_not_removed(self):
        path = Mock()
        for error in (PermissionError(errno.EACCES, 'mock denied'), OSError(errno.EIO, 'mock I/O')):
            path.stat.side_effect = error
            with self.assertRaises(OSError):
                waiter.path_absent(path)
        path.stat.side_effect = FileNotFoundError
        self.assertTrue(waiter.path_absent(path))

    def test_recreated_cgroup_or_unreadable_events_fail_closed(self):
        value = self.watch(); value.path.stat.return_value = SimpleNamespace(st_ino=999)
        with patch.object(waiter.os, 'lseek'), patch.object(waiter.os, 'read', return_value=b'populated 0\n'):
            with self.assertRaises(RuntimeError):
                value.sample()
        value.fd = None
        with self.assertRaises(RuntimeError):
            value.sample()
        value.group.stat.side_effect = FileNotFoundError
        self.assertIsNone(value.sample())


class ReplayTests(unittest.TestCase):
    def test_complete_replay_before_evaluation_tracks_last_vm_and_cursor(self):
        e = entry(); item = waiter.new_witness(e)
        rows = [start_row(e), security_row(e), controller_row(e), start_row(e, clock=20), manager_row(e)]
        process = Mock(); process.stdout = io.BytesIO(b''.join(json.dumps(row).encode() + b'\n' for row in rows)); process.wait.return_value = 0; process.poll.return_value = 0
        with patch.object(waiter.subprocess, 'Popen', return_value=process):
            cursor = waiter.replay(manifest(), [e], {1: item})
        self.assertEqual(cursor, rows[-1]['__CURSOR'])
        self.assertFalse(item['completed_vm']); self.assertFalse(item['drained'])
        process.terminate.assert_not_called()

    def test_replay_stream_failure_and_bounded_rows_fail_closed(self):
        process = Mock(); process.stdout = io.BytesIO(b''); process.wait.return_value = 1; process.poll.return_value = 1
        with patch.object(waiter.subprocess, 'Popen', return_value=process):
            with self.assertRaises(RuntimeError):
                waiter.replay(manifest(), [entry()], {1: waiter.new_witness(entry())})
        for line in (b'{}\n', b'[]\n', b'{"__CURSOR":"valid"}', b'x' * (waiter.MAX_ROW + 1)):
            with self.assertRaises((RuntimeError, json.JSONDecodeError)):
                waiter.journal_row(line)

    def test_all_slot_full_boot_history_total_bound_fails_closed(self):
        m = manifest(); state = {e['slot']: waiter.new_witness(e) for e in m['controllers']}
        rows = []
        for e in m['controllers']:
            for i in range(33):
                rows.extend((start_row(e, 3 * i + 1), security_row(e, 3 * i + 2), controller_row(e, clock=3 * i + 3)))
        process = Mock(); process.stdout = io.BytesIO(b''.join(json.dumps(row).encode() + b'\n' for row in rows)); process.poll.return_value = None
        with patch.object(waiter.subprocess, 'Popen', return_value=process), self.assertRaisesRegex(RuntimeError, 'All-controller root VM history bound'):
            waiter.replay(m, m['controllers'], state)
        process.terminate.assert_called_once()  # Owned reader only.


class FakeGroup:
    def __init__(self, e, absent=False):
        self.fd = None if absent else 200 + e['slot']
        self.closed = False
        self.absent = absent
        self.samples = []

    def sample(self, event=0):
        self.samples.append(event)
        return None if self.absent else False

    def close(self):
        self.closed = True; self.fd = None


class EventLoopTests(unittest.TestCase):
    def setUp(self):
        # Host-free: the kernel boot read is the one simulated input here.
        patcher = patch.object(waiter, 'kernel_boot_id', return_value=BOOT)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_other_boot_fails_before_pidfd_watch_journal_or_public_reads(self):
        m = manifest(); drain, _ = self.make_drain(lambda slot: '0')
        with patch.object(waiter, 'kernel_boot_id', return_value='2c0a7d1e-0000-4000-8000-00000000b007'), \
             patch.object(waiter.os, 'pidfd_open') as opened, patch.object(waiter.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'another boot'): waiter.wait_drained(drain, m)
            opened.assert_not_called(); spawn.assert_not_called()
        drain.public_registration.assert_not_called(); drain.save.assert_not_called()

    def make_drain(self, current):
        snapshots = []
        drain = SimpleNamespace(STATE=waiter.STATE, properties=Mock(side_effect=lambda slot: values(entry(slot), main=current(slot))),
                                starttime=Mock(side_effect=lambda pid: str(pid + 900)), public_registration=Mock(return_value=None),
                                save=Mock(side_effect=lambda value: snapshots.append(copy.deepcopy(value))), timestamp=lambda: '2026-10-05T12:40:00+00:00')
        return drain, snapshots

    def test_lost_wake_pidfd_and_subtree_terminal_before_mainpid_clears(self):
        m = manifest(); cleared = False
        drain, snapshots = self.make_drain(lambda slot: '0' if cleared else str(entry(slot)['pid']))
        poller = Mock()
        poller.poll.side_effect = [
            [(100 + slot, select.POLLIN) for slot in range(1, 5)],
            [(200 + slot, select.POLLPRI | select.POLLERR) for slot in range(1, 5)],
            [(90, select.POLLIN)],
        ]
        groups = {}
        def group(e):
            groups[e['slot']] = FakeGroup(e); return groups[e['slot']]
        def replay(m, entries, state, cursor=None):
            if cursor is None:
                for e in entries:
                    lifecycle(e, state[e['slot']])
            return 'cursor-after-replay'
        journal = Mock(); journal.stdout.fileno.return_value = 90
        def read(fd, size):
            nonlocal cleared
            self.assertEqual(fd, 90)
            self.assertTrue(all(not g.closed for g in groups.values()))
            cleared = True
            return b''.join(json.dumps(manager_row(e)).encode() + b'\n' for e in m['controllers'])
        with patch.object(waiter.select, 'poll', return_value=poller), patch.object(waiter.os, 'pidfd_open', side_effect=lambda pid: pid), patch.object(waiter.os, 'close'), patch.object(waiter.os, 'read', side_effect=read), patch.object(waiter, 'CgroupWatch', side_effect=group), patch.object(waiter, 'replay', side_effect=replay), patch.object(waiter.subprocess, 'Popen', return_value=journal), patch('builtins.print'):
            waiter.wait_drained(drain, m)
        self.assertEqual(m['phase'], waiter.HARDWARE_PHASE)
        self.assertEqual(poller.poll.call_args_list, [unittest.mock.call()] * 3)  # no timed polling
        self.assertTrue(all(not any(i['drained'] for i in s['drain_witness'].values()) for s in snapshots if s['phase'] == 'armed-awaiting-job-completion' and not any(i['manager_terminal'] for i in s['drain_witness'].values())))
        self.assertTrue(all(i['drained'] for i in m['drain_witness'].values()))
        self.assertEqual(drain.public_registration.call_count, 8)
        self.assertTrue(all(select.POLLPRI | select.POLLERR in g.samples for g in groups.values()))

    def run_terminal_mock(self, replay_hook, public=None, receipt_value=None):
        m = manifest(); drain, snapshots = self.make_drain(lambda slot: '0')
        if callable(public):
            drain.public_registration.side_effect = public
        else:
            drain.public_registration.return_value = public
        poller = Mock(); poller.poll.side_effect = RuntimeError('MOCK_WAIT_BLOCKED_WITHOUT_TIMER')
        journal = Mock(); journal.stdout.fileno.return_value = 90
        def group(e): return FakeGroup(e, absent=True)
        def replay(m, entries, state, cursor=None):
            replay_hook(entries, state, cursor)
            return 'replay-cursor'
        if receipt_value is None:
            receipt_mock = patch.object(waiter, 'read_public_json', side_effect=FileNotFoundError)
        elif callable(receipt_value):
            receipt_mock = patch.object(waiter, 'read_public_json', side_effect=receipt_value)
        else:
            receipt_mock = patch.object(waiter, 'read_public_json', return_value=receipt_value)
        with patch.object(waiter.select, 'poll', return_value=poller), patch.object(waiter.os, 'pidfd_open', side_effect=ProcessLookupError), patch.object(waiter, 'CgroupWatch', side_effect=group), patch.object(waiter, 'replay', side_effect=replay), patch.object(waiter.subprocess, 'Popen', return_value=journal), receipt_mock, patch('builtins.print') as printed:
            drain.printed = printed
            try:
                waiter.wait_drained(drain, m)
            except RuntimeError as error:
                return m, drain, snapshots, str(error)
        return m, drain, snapshots, None

    def test_zero_work_missing_vm_remains_blocked_not_assumed_success(self):
        def replay(entries, state, cursor):
            for e in entries:
                waiter.consume_journal(e, state[e['slot']], manager_row(e))
        m, drain, _, error = self.run_terminal_mock(replay)
        self.assertEqual(error, 'MOCK_WAIT_BLOCKED_WITHOUT_TIMER')
        self.assertEqual(m['phase'], 'armed-awaiting-job-completion')
        drain.public_registration.assert_not_called()

    def test_final_catchup_prevents_hardware_witness_for_later_started_vm(self):
        def replay(entries, state, cursor):
            if cursor is None:
                for e in entries:
                    lifecycle(e, state[e['slot']])
                    waiter.consume_journal(e, state[e['slot']], manager_row(e))
            else:
                waiter.consume_journal(entries[0], state[1], start_row(entries[0], clock=40))
        m, _, _, error = self.run_terminal_mock(replay)
        self.assertEqual(error, 'MOCK_WAIT_BLOCKED_WITHOUT_TIMER')
        self.assertEqual(m['phase'], 'armed-awaiting-job-completion')
        self.assertFalse(m['drain_witness'][1]['completed_vm'])

    def test_null_public_registration_without_receipt_fails_closed(self):
        def replay(entries, state, cursor):
            for e in entries:
                lifecycle(e, state[e['slot']])
                waiter.consume_journal(e, state[e['slot']], manager_row(e))
        m, _, _, error = self.run_terminal_mock(replay, public=registration())
        self.assertIn('Uncertain public registration', error)
        self.assertEqual(m['phase'], 'armed-awaiting-job-completion')

    def test_four_exact_blocked_receipts_allow_only_four_positive_final_witnesses(self):
        def replay(entries, state, cursor):
            for e in entries:
                lifecycle(e, state[e['slot']])
                waiter.consume_journal(e, state[e['slot']], manager_row(e))
        def blocked(path, limit):
            self.assertEqual(limit, 4096)
            slot = int(path.name.removeprefix('blocked-jit-').removesuffix('.json'))
            return receipt(entry(slot))
        m, drain, _, error = self.run_terminal_mock(replay, public=lambda slot: registration(entry(slot)), receipt_value=blocked)
        self.assertIsNone(error)
        self.assertEqual(m['phase'], waiter.HARDWARE_PHASE)
        self.assertEqual(set(m['drain_witness']), waiter.SLOTS)
        self.assertTrue(all(i['drained'] and i['registration_witness']['kind'] == 'exact-local-post-blocked' for i in m['drain_witness'].values()))
        self.assertEqual(drain.public_registration.call_count, 8)

    def test_pid_reuse_fails_before_public_record_or_journal_queries(self):
        m = manifest(); drain, _ = self.make_drain(lambda slot: str(entry(slot)['pid']))
        drain.starttime.return_value = 'reused'; drain.starttime.side_effect = None
        with patch.object(waiter.select, 'poll'), patch.object(waiter.os, 'pidfd_open', return_value=91), patch.object(waiter.os, 'close'), patch.object(waiter, 'replay') as replay:
            with self.assertRaisesRegex(RuntimeError, 'Old PID reused'):
                waiter.wait_drained(drain, m)
        replay.assert_not_called(); drain.public_registration.assert_not_called()

    def test_pushed_guest_finished_is_never_actions_job_certificate(self):
        def replay(entries, state, cursor):
            for e in entries:
                if cursor is None:
                    lifecycle(e, state[e['slot']], preflight=False, completed=False)
                    self.assertFalse(waiter.consume_journal(e, state[e['slot']], controller_row(e, 'HOUND_CI_RUNNER_FINISHED', clock=20)))
                    waiter.consume_journal(e, state[e['slot']], manager_row(e))
        m, drain, _, error = self.run_terminal_mock(replay)
        self.assertIsNone(error)
        self.assertEqual(m['phase'], 'all-four-hardware-drained-awaiting-actions-proof')
        self.assertIs(m['job_certified'], False)
        self.assertTrue(all(i['drained'] and not i['completed_vm'] and not i['job_certified'] for i in m['drain_witness'].values()))
        output = '\n'.join(call.args[0] for call in drain.printed.call_args_list)
        self.assertIn('NOT_JOB_CERTIFIED', output)
        self.assertIn('actions-proof=pending', output)
        self.assertNotIn('no-busy-job-kill', output)
        self.assertNotIn('all-four-drained-awaiting-replacement', m['phase'])

    def test_final_all_four_properties_recheck_detects_removed_hold(self):
        def replay(entries, state, cursor):
            for e in entries:
                if cursor is None:
                    lifecycle(e, state[e['slot']])
                    waiter.consume_journal(e, state[e['slot']], manager_row(e))
        calls = {slot: 0 for slot in waiter.SLOTS}
        def current(slot):
            calls[slot] += 1
            result = values(entry(slot))
            if slot == 4 and calls[slot] == 2:
                result['Restart'] = 'always'
            return result
        m = manifest(); drain, _ = self.make_drain(lambda slot: '0')
        drain.properties.side_effect = current
        journal = Mock(); journal.stdout.fileno.return_value = 90
        def replay_mock(m, entries, state, cursor=None):
            replay(entries, state, cursor)
            return 'replay-cursor'
        with patch.object(waiter.select, 'poll'), patch.object(waiter.os, 'pidfd_open', side_effect=ProcessLookupError), patch.object(waiter, 'CgroupWatch', side_effect=lambda e: FakeGroup(e, absent=True)), patch.object(waiter, 'replay', side_effect=replay_mock), patch.object(waiter.subprocess, 'Popen', return_value=journal), patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'Drain hold unexpectedly removed'):
                waiter.wait_drained(drain, m)
        self.assertEqual(m['phase'], 'armed-awaiting-job-completion')
        self.assertEqual(calls, {slot: 2 for slot in waiter.SLOTS})


if __name__ == '__main__':
    unittest.main()
