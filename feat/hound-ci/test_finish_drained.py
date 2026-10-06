#!/usr/bin/env python3
"""Fully mocked finish-drain tests: no API/root/kernel/mount/signal/host reads."""
import builtins
import copy
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import errno
import importlib.util
import io
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('finish', Path(__file__).with_name('finish-drain.py'))
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)
wait_spec = importlib.util.spec_from_file_location('real_waiter', Path(__file__).with_name('wait-drained.py'))
real_waiter = importlib.util.module_from_spec(wait_spec)
wait_spec.loader.exec_module(real_waiter)
act_spec = importlib.util.spec_from_file_location('real_activation', Path(__file__).with_name('activate-cache-v2.py'))
real_activation = importlib.util.module_from_spec(act_spec)
act_spec.loader.exec_module(real_activation)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat('2026-10-05T12:31:00+00:00')


_clock_patch = patch.object(finish, 'datetime', FrozenDateTime)
_python_patch = patch.object(finish, 'python_executable', return_value=str(finish.PYTHON.with_name('python3.14')))


def setUpModule():
    _clock_patch.start()
    _python_patch.start()


def tearDownModule():
    _python_patch.stop()
    _clock_patch.stop()


BOOT = '1b0a7d1e-0000-4000-8000-00000000b007'
BOOT_HEX = BOOT.replace('-', '')


def invocation(slot):
    return f'{slot:x}' * 32


def manifest():
    result = {'phase': finish.HARDWARE_PHASE, 'boot_id': BOOT, 'drain_nonce': '1b123456-1234-4123-8123-123456789abc',
              'created_utc': '2026-10-05T12:20:00+00:00', 'drained_utc': '2026-10-05T12:30:00+00:00',
              'witness_since': finish.WITNESS_SINCE,
              'armed': [{'slot': slot, 'utc': '2026-10-05T12:20:31+00:00'} for slot in range(1, 5)],
              'original_elf_sha256': 'e' * 64, 'controllers': [], 'gates': {}, 'drain_witness': {}}
    for number, (path, sha) in enumerate(finish.SOURCE_FIELDS):
        result[path] = '/nix/store/' + str(number) + '-' + path + '.py'
        result[sha] = str(number) * 64
    result.update(old_source=finish.OLD_SOURCE, old_source_sha256=finish.OLD_SOURCE_SHA256)
    for slot in range(1, 5):
        entry = {'slot': slot, 'pid': 100 + slot, 'starttime': str(1000 + slot),
                 'qemu_uid': 980 + slot, 'qemu_gid': 970 + slot,
                 'invocation_id': invocation(slot), 'boot_id': BOOT,
                 'control_group': f'/hound.slice/hound-ci.slice/hound-ci-{slot}.service'}
        result['controllers'].append(entry)
        vm = {'name': f'hound-ci-{slot}-{slot:012x}', 'start_monotonic': '20000000', 'stop_monotonic': '30000000',
              'start_realtime': str(finish.micros('2026-10-05T12:10:00+00:00')),
              'stop_realtime': str(finish.micros('2026-10-05T12:29:00+00:00')),
              'qemu_pid': 400 + slot, 'security_verified': True}
        registration = {'repo': finish.REPO, 'id': 200 + slot, 'name': vm['name']}
        result['gates'][str(slot)] = {'stage': 'armed', 'registration': registration,
            'registration_after_gate': copy.deepcopy(registration),
            'observed_utc': '2026-10-05T12:20:30+00:00',
            'observation_started_monotonic_us': '25000000',
            'observation_started_utc': '2026-10-05T12:20:29+00:00', 'observation_boot_id': BOOT,
            'first_registration_read': {'path': f'/var/lib/hound-ci/slot-{slot}-registration.json', 'present': True,
                                        'boot_id': BOOT, 'after_monotonic_us': '25000000',
                                        'operator_sha256': result['operator_sha256']},
            'host_qemu_before_gate': {'pid': vm['qemu_pid'], 'starttime': '2000', 'parent_pid': entry['pid'],
                'uid': entry['qemu_uid'], 'gid': entry['qemu_gid'], 'disk': f'/var/lib/hound-ci/slot-{slot}/job.qcow2',
                'isolation_verified': True, 'executable': finish.QEMU_ELF}}
        result['drain_witness'][str(slot)] = {
            'old_pid': entry['pid'], 'starttime': entry['starttime'], 'vm_history': [vm],
            'controller_exited': True, 'cgroup_empty': True, 'drained': True, 'cgroup_removed': True,
            'manager_terminal': sorted(finish.MANAGER_IDS)[0], 'manager_monotonic': '40000000',
            'latest_vm': {'kind': 'stopped', 'name': vm['name'], 'preflight': False, 'runner_completed': False},
            'latest_vm_monotonic': vm['stop_monotonic'], 'registration_witness': {'kind': 'public-record-absent'}}
    return result


def sync_first_read(m):
    # The armer records the FIRST read verbatim as gate['registration'] plus a
    # present flag; fixtures that rewrite the snapshot must keep them equal.
    for gate in m['gates'].values():
        first = gate['first_registration_read']
        first['present'] = gate.get('registration') is not None
        if isinstance(gate.get('observation_started_monotonic_us'), str):
            first['after_monotonic_us'] = gate['observation_started_monotonic_us']
    return m


def cleanup(m, slot=1, runner_id=None, name=None):
    entry = m['controllers'][slot - 1]
    return {'slot': slot, 'old_pid': entry['pid'], 'starttime': entry['starttime'], 'repo': finish.REPO,
            'id': runner_id or 200 + slot, 'name': name or m['drain_witness'][str(slot)]['vm_history'][-1]['name'],
            'drain_nonce': m['drain_nonce'], 'gate_sha256': m['gate_sha256'], 'stage': 'delete-returned',
            'success': True, 'returncode': 0, 'utc': '2026-10-05T12:29:01+00:00'}


def cleanups(m, first=None):
    return [first or cleanup(m)] + [cleanup(m, slot) for slot in range(2, 5)]


def historical_manifest():
    # Full authenticated root STOP precedes gate observation + arm receipt;
    # neither pre/post snapshot retains any positive registration for that VM.
    m = manifest()
    for slot in range(1, 5):
        vm = m['drain_witness'][str(slot)]['vm_history'][-1]
        vm['stop_realtime'] = str(finish.micros('2026-10-05T12:19:00Z'))
        vm['stop_monotonic'] = '24000000'
        m['drain_witness'][str(slot)]['latest_vm_monotonic'] = vm['stop_monotonic']
        m['gates'][str(slot)].update(registration=None, registration_after_gate=None, host_qemu_before_gate=None)
        m['gates'][str(slot)]['first_registration_read']['present'] = False
    return m


def post_gate_manifest():
    m = manifest(); w = m['drain_witness']['1']
    # A root-authenticated pre-gate inflight POST may become a VM after the
    # gate: never cancel it or assume missing pre/post registration is cleanup.
    w['vm_history'][0]['stop_realtime'] = str(finish.micros('2026-10-05T12:21:00Z'))
    vm = copy.deepcopy(w['vm_history'][0])
    vm.update(name='hound-ci-1-abcdef012345', start_monotonic='31000000', stop_monotonic='35000000',
              start_realtime=str(finish.micros('2026-10-05T12:22:00Z')),
              stop_realtime=str(finish.micros('2026-10-05T12:29:00Z')), qemu_pid=901)
    w['vm_history'].append(vm)
    w['latest_vm'].update(name=vm['name']); w['latest_vm_monotonic'] = vm['stop_monotonic']
    m['gates']['1']['registration_after_gate'] = None
    return m


def job(slot, job_id=None, run_id=500, attempt=1, conclusion='success'):
    job_id = job_id or 1000 + slot
    return {'id': job_id, 'run_id': run_id, 'run_attempt': attempt,
            'url': f'https://api.github.com/repos/{finish.REPO}/actions/jobs/{job_id}',
            'run_url': f'https://api.github.com/repos/{finish.REPO}/actions/runs/{run_id}',
            'runner_name': f'hound-ci-{slot}-{slot:012x}', 'runner_id': 200 + slot,
            'status': 'completed', 'conclusion': conclusion,
            'started_at': '2026-10-05T09:00:00Z', 'completed_at': '2026-10-05T12:28:00Z'}


def run_row(run_id=500, attempt=1, created='2026-10-05T12:00:00Z', updated='2026-10-05T12:30:00Z', status='completed'):
    return {'id': run_id, 'run_attempt': attempt, 'repository': {'full_name': finish.REPO},
            'url': f'https://api.github.com/repos/{finish.REPO}/actions/runs/{run_id}',
            'status': status, 'created_at': created, 'updated_at': updated}


def window_bounds(route):
    created = route.split('created=', 1)[1].split('&', 1)[0]
    start, end = created.split('..')
    return finish.micros(start.replace('Z', '+00:00')), finish.micros(end.replace('Z', '+00:00')) + 999999


@contextmanager
def fake_gh_config(order):
    # Models install/check/remove on a private temp path: ownership semantics
    # are real (the run_producer finally decides), file metadata is not.
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / 'hound-ci-actions-gh'
        def install(ownership):
            target.mkdir(); ownership['created'] = True; order.append('install')
        def remove():
            order.append('remove'); target.rmdir()
        with patch.object(finish, 'GH_CONFIG_DIR', target), patch.object(finish, 'install_gh_config', side_effect=install), \
             patch.object(finish, 'check_gh_config', side_effect=lambda root=False: order.append(('check', root))), \
             patch.object(finish, 'remove_gh_config', side_effect=remove):
            yield target


class FakeAPI:
    def __init__(self):
        run = run_row()
        self.runs = [run]
        self.attempts = {1: copy.deepcopy(run)}
        self.jobs = {1: [job(slot) for slot in range(1, 5)]}
        self.direct = {row['id']: copy.deepcopy(row) for row in self.jobs[1]}
        self.calls = 0
        self.pages = []
        self.windows = []
        self.split_over = None   # more runs than this: the cap is reached (1 call)
        self.unconverged = set()  # routes that never converge (3 passes)

    def window(self, route):
        # A converged closed window: two identical single-page passes.
        self.windows.append(route)
        start, end = window_bounds(route)
        result = [run for run in self.runs if start <= finish.micros(run['created_at']) <= end]
        for calls, splits in ((1, self.split_over is not None and len(result) > self.split_over),
                              (3, route in self.unconverged)):
            if splits:
                self.calls += calls
                split = finish.WindowSplit('fake split'); split.calls = calls
                raise split
        self.calls += 2
        self.pages.append({'route': route, 'key': 'workflow_runs', 'total_count': len(result),
                           'pages': 1, 'passes': [1, 1]})
        return copy.deepcopy(result)

    def paged(self, route, key):
        self.calls += 1
        result = self.jobs[int(route.split('/')[-2])]
        self.pages.append({'route': route, 'key': key, 'pages': 1, 'total_count': len(result)})
        return copy.deepcopy(result)

    def get(self, route):
        self.calls += 1
        if '/attempts/' in route:
            return copy.deepcopy(self.attempts[int(route.split('/')[-1])])
        return copy.deepcopy(self.direct[int(route.split('/')[-1])])


def collection(m=None):
    m = m or manifest()
    api = FakeAPI()
    targets = finish.accepted_vms(m)
    for index, vm in enumerate(targets):
        if vm['name'] not in {row['runner_name'] for row in api.jobs[1]}:
            row = job(vm['slot'], job_id=2000 + index)
            row.update(runner_name=vm['name'], runner_id=vm['runner_id'] or 901)
            api.jobs[1].append(row)
    for vm in targets:
        # Keep real-root history + mocked API wallclock in the same lifecycle.
        # Individual timestamp-negative tests deliberately override these.
        end = int(vm['final_accepted_vm']['stop_realtime'])
        for row in api.jobs[1]:
            if row['runner_name'] == vm['name']:
                row['completed_at'] = datetime.fromtimestamp((end - 1000000) / 1000000, timezone.utc).isoformat()
    api.direct = {row['id']: copy.deepcopy(row) for row in api.jobs[1]}
    return finish.collect(m, api)


def invocation_fixture(m, report):
    data = finish.canonical(m) + b'\n'
    intent = finish.new_capture_intent(m, data)
    intent.update(invocation_id='9b123456-1234-4123-8123-123456789abc',
                  started_utc='2026-10-05T12:30:30Z', started_monotonic_ns='50000000000')
    output = finish.canonical(report) + b'\n'
    boundary = {'pid': 9999, 'starttime': '4444', 'uids': [1000] * 4, 'gids': [finish.COLLECTOR_GID] * 4,
                'groups': [], 'caps': [0] * 5, 'no_new_privs': 1, 'executable': finish.python_executable()}
    actual = {'child_pid': 9999, 'child_exit': 0, 'boundary': boundary,
              'ready_fd': '11', 'ack_fd': '12', 'argv_sha256': finish.digest(finish.canonical(finish.producer_argv(m, 11, 12)))}
    receipt = {'schema': 1, 'kind': 'root-fixed-collector-invocation', 'intent': intent, **actual,
               'output_sha256': finish.digest(output), 'output_bytes': len(output),
               'finished_utc': '2026-10-05T12:31:01Z', 'finished_monotonic_ns': '81000000000',
               'request_count': report['request_count'], 'response_identity_sha256': finish.digest(finish.canonical(report['jobs']))}
    return receipt


def certificate(m=None):
    m = m or manifest()
    report = collection(m)
    return {'schema': 1, 'kind': 'root-actions-terminal-certificate',
            'certified_utc': '2026-10-05T12:32:00+00:00',
            'collection_sha256': finish.digest(finish.canonical(report)), 'collection': report,
            'cleanup_receipts': cleanups(m), 'invocation': invocation_fixture(m, report)}


class LifecycleIdentityTests(unittest.TestCase):
    def test_guest_flags_advisory_terminal_failed_and_cancelled_truthful(self):
        m = manifest()
        self.assertEqual(len(finish.accepted_vms(m)), 4)
        api = FakeAPI()
        api.jobs[1][0]['conclusion'] = 'failure'
        api.jobs[1][1]['conclusion'] = 'cancelled'
        api.direct = {row['id']: copy.deepcopy(row) for row in api.jobs[1]}
        report = finish.collect(m, api)
        self.assertEqual([row['job']['conclusion'] for row in report['jobs'][:2]], ['failure', 'cancelled'])
        finish.validate_collection(report, m)

    def test_full_phase_nonce_source_provenance_required(self):
        for mutate in (
            lambda m: m.update(phase='all-four-drained-awaiting-replacement'),
            lambda m: m.pop('drain_nonce'),
            lambda m: m.update(drain_nonce='not-a-uuid'),
            lambda m: m.update(drain_nonce='1B123456-1234-4123-8123-123456789ABC'),
            lambda m: m.pop('gate_sha256'),
            lambda m: m.update(waiter_source='/tmp/waiter.py'),
            lambda m: m.update(waiter_source=m['operator_source']),
            lambda m: m['controllers'].pop(),
            lambda m: m['controllers'][0].update(pid=m['controllers'][1]['pid']),
            lambda m: m['controllers'][0].update(control_group='/wrong'),
            lambda m: m['gates']['1'].update(stage='bind-intent'),
        ):
            m = manifest(); mutate(m)
            with self.subTest(m=m), self.assertRaises(RuntimeError):
                finish.accepted_vms(m)

    def test_all_histories_and_latest_identity_order_fail_closed(self):
        for mutate in (
            lambda w: w.pop('vm_history'), lambda w: w.update(vm_history=[]),
            lambda w: w['vm_history'][0].update(name='guest-supplied-name'),
            lambda w: w['vm_history'][0].update(provenance='root-serial-marker'),
            lambda w: w['vm_history'][0].update(start_monotonic='30000001'),
            lambda w: w['vm_history'][0].update(stop_monotonic=None),
            lambda w: w['vm_history'][0].update(qemu_pid=None, security_verified=False),
            lambda w: w['vm_history'][0].update(qemu_pid=True),
            lambda w: w.update(manager_monotonic='29999999'),
            lambda w: w.update(latest_vm_monotonic='30000001'),
            lambda w: w['latest_vm'].update(kind='started'),
            lambda w: w['latest_vm'].update(name='hound-ci-1-000000000002'),
            lambda w: w.update(manager_terminal='human-readable-stop-text'),
            lambda w: w.update(controller_exited=False), lambda w: w.update(cgroup_empty=False),
            lambda w: w.update(cgroup_removed='true'),
        ):
            m = manifest(); mutate(m['drain_witness']['1'])
            with self.subTest(w=m['drain_witness']['1']), self.assertRaises(RuntimeError):
                finish.accepted_vms(m)

    def test_completed_A_never_certifies_B_or_omits_pregate_adopted_VM(self):
        m = manifest(); w = m['drain_witness']['1']
        prior = copy.deepcopy(w['vm_history'][0])
        prior.update(name='hound-ci-1-abcdef012345', start_monotonic='11000000', stop_monotonic='19000000',
                     start_realtime=str(finish.micros('2026-10-05T11:55:00+00:00')),
                     stop_realtime=str(finish.micros('2026-10-05T12:09:00+00:00')), qemu_pid=399)
        w['vm_history'].insert(0, prior)
        # Arm boundary BEFORE A's STOP: A overlaps arming and must be adopted.
        m['gates']['1']['observation_started_monotonic_us'] = '18000000'; sync_first_read(m)
        self.assertEqual(len(finish.accepted_vms(m)), 5)
        with self.assertRaisesRegex(RuntimeError, 'no-match/ambiguous'): finish.collect(m, FakeAPI())
        with self.assertRaises(RuntimeError): finish.validate_collection(collection(), m)

    def test_gate_generated_hex_nonce_is_supported(self):
        m = manifest(); m['drain_nonce'] = m['drain_nonce'].replace('-', '')
        self.assertEqual(len(finish.accepted_vms(m)), 4)
        finish.validate_collection(collection(m), m)

    def test_root_registration_id_disagreement_and_type_fail(self):
        m = manifest(); m['gates']['1']['registration_after_gate']['id'] = 999
        with self.assertRaises(RuntimeError): finish.accepted_vms(m)
        for key in ('registration', 'registration_after_gate'):
            m = manifest(); m['gates']['1'][key]['id'] = True
            with self.assertRaises(RuntimeError): finish.accepted_vms(m)

    def test_actual_qemu_binding_not_guest_markers(self):
        for key, value in [('pid', 999), ('uid', 999), ('gid', 999), ('parent_pid', 999),
                           ('disk', '/other/job.qcow2'), ('isolation_verified', False), ('executable', '/bin/qemu')]:
            m = manifest(); m['gates']['1']['host_qemu_before_gate'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): finish.accepted_vms(m)

    def test_pregate_positive_B_without_root_start_security_cannot_disappear(self):
        for key in ('registration', 'registration_after_gate'):
            m = manifest(); m['gates']['1'][key].update(name='hound-ci-1-abcdef012345', id=999)
            with self.subTest(key=key), self.assertRaises(RuntimeError): finish.accepted_vms(m)

    def test_unknown_api_id_allowed_only_single_exact_root_authenticated_name(self):
        m = manifest(); m['gates']['1'].update(registration=None, registration_after_gate=None, host_qemu_before_gate=None)
        sync_first_read(m)
        self.assertIsNone(finish.accepted_vms(m)[0]['runner_id'])
        finish.validate_collection(collection(m), m)
        api = FakeAPI(); api.jobs[1][0]['runner_id'] = 999; api.direct[1001]['runner_id'] = 999
        report = finish.collect(m, api)
        self.assertEqual(report['jobs'][0]['job']['runner_id'], 999)
        with self.assertRaises(RuntimeError): finish.validate_collection(report, m, cleanups(m))
        finish.validate_collection(report, m, cleanups(m, cleanup(m, runner_id=999)))
        api.jobs[1].append(job(1, job_id=2001)); api.direct[2001] = job(1, job_id=2001)
        with self.assertRaises(RuntimeError): finish.collect(m, api)

    def test_root_unknown_id_collision_holds(self):
        m = manifest()
        for gate in m['gates'].values(): gate.update(registration=None, registration_after_gate=None, host_qemu_before_gate=None)
        sync_first_read(m)
        api = FakeAPI(); api.jobs[1][1]['runner_id'] = 201; api.direct[1002]['runner_id'] = 201
        with self.assertRaisesRegex(RuntimeError, 'runner ID collision'): finish.collect(m, api)

    def test_uncertain_null_only_adopted_or_exact_local_block(self):
        m = manifest(); g = m['gates']['1']; g['host_qemu_before_gate'] = None
        g['registration']['id'] = None; g['registration_after_gate']['id'] = None
        finish.validate_collection(collection(m), m)
        g['registration'].update(name='hound-ci-1-abcdef012345')
        with self.assertRaisesRegex(RuntimeError, 'Uncertain NULL'): finish.accepted_vms(m)
        e = m['controllers'][0]
        m['drain_witness']['1']['registration_witness'] = {
            'kind': 'exact-local-post-blocked', 'slot': 1, 'old_pid': e['pid'], 'starttime': e['starttime'],
            'repo': finish.REPO, 'name': g['registration']['name'], 'route_blocked': True,
            'utc': '2026-10-05T12:20:31+00:00'}
        self.assertEqual(len(finish.accepted_vms(m)), 4)
        m['drain_witness']['1']['registration_witness']['drain_nonce'] = m['drain_nonce']
        with self.assertRaises(RuntimeError): finish.accepted_vms(m)

    def test_arm_overlapping_history_selected_earlier_history_excluded(self):
        # arm-overlap-v1: adopt iff STOP monotonic >= the slot's gate boundary
        # (25000000), a gate registration names it, or it is the last VM. A
        # post-approval (>= 11:56) VM which ended before arming is history.
        m = manifest(); w = m['drain_witness']['1']; prior = copy.deepcopy(w['vm_history'][0])
        prior.update(name='hound-ci-1-abcdef012345', start_monotonic='11000000', stop_monotonic='19000000',
                     start_realtime=str(finish.micros('2026-10-05T12:00:00+00:00')),
                     stop_realtime=str(finish.micros('2026-10-05T12:05:00+00:00')), qemu_pid=399)
        w['vm_history'].insert(0, prior)
        self.assertEqual([vm['name'] for vm in finish.accepted_vms(m) if vm['slot'] == 1], [w['vm_history'][1]['name']])
        for stop, adopted in (('24999999', False), ('25000000', True)):
            current = copy.deepcopy(m); cw = current['drain_witness']['1']
            cw['vm_history'][0]['stop_monotonic'] = stop
            cw['vm_history'][1].update(start_monotonic='26000000')
            names = [vm['name'] for vm in finish.accepted_vms(current) if vm['slot'] == 1]
            with self.subTest(stop=stop):
                self.assertEqual(prior['name'] in names, adopted)
        # A gate registration naming the earlier VM adopts it regardless.
        current = copy.deepcopy(m)
        current['gates']['1']['registration'] = {'repo': finish.REPO, 'id': 299, 'name': prior['name']}
        current['gates']['1']['host_qemu_before_gate'] = None; sync_first_read(current)
        self.assertIn(prior['name'], [vm['name'] for vm in finish.accepted_vms(current)])

    def test_busy_slot_earlier_adopted_VMs_are_DELETE_covered_by_same_process_successor(self):
        # Busy slot at arm: VMs A, B stopped AFTER the arm boundary (adopted)
        # and C is the last VM. A and B each have a later START in the same
        # pinned process history, which the legacy worker reaches only after
        # their cleanup DELETE returned 0/404. Only C needs its gate receipt.
        m = manifest(); w = m['drain_witness']['1']; last = w['vm_history'][0]
        earlier = []
        for index, (start, stop) in enumerate((('25100000', '25200000'), ('25300000', '25400000'))):
            vm = copy.deepcopy(last)
            vm.update(name=f'hound-ci-1-00000000a{index:03x}', start_monotonic=start, stop_monotonic=stop,
                      start_realtime=str(finish.micros(f'2026-10-05T12:2{index + 1}:00+00:00')),
                      stop_realtime=str(finish.micros(f'2026-10-05T12:2{index + 1}:30+00:00')), qemu_pid=700 + index)
            earlier.append(vm)
        last.update(start_monotonic='25500000', start_realtime=str(finish.micros('2026-10-05T12:23:00+00:00')))
        w['vm_history'][:0] = earlier
        m['gates']['1']['registration'] = {'repo': finish.REPO, 'id': 299, 'name': earlier[0]['name']}
        m['gates']['1']['host_qemu_before_gate'] = None
        sync_first_read(m)
        adopted = [vm['name'] for vm in finish.accepted_vms(m) if vm['slot'] == 1]
        self.assertEqual(adopted, [earlier[0]['name'], earlier[1]['name'], last['name']])
        known = finish.validate_cleanup_receipts(m, cleanups(m))
        self.assertEqual((known[earlier[0]['name']], known[earlier[1]['name']]), (299, None))
        finish.validate_collection(collection(m), m, cleanups(m))
        # Negative: the LAST VM has no later START, so it still needs proof.
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, cleanups(m)[1:])
        # Negative: without its successor (history ends at A), A needs proof.
        cut = copy.deepcopy(m); cw = cut['drain_witness']['1']
        del cw['vm_history'][1:]
        cw['latest_vm'].update(name=earlier[0]['name']); cw['latest_vm_monotonic'] = earlier[0]['stop_monotonic']
        cut['gates']['1']['registration_after_gate'] = None
        cut['gates']['1']['registration'] = {'repo': finish.REPO, 'id': 299, 'name': earlier[0]['name']}
        sync_first_read(cut)
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(cut, cleanups(cut)[1:])
        finish.validate_cleanup_receipts(cut, cleanups(cut, cleanup(cut, runner_id=299)))
        # A positive receipt that never returned still HOLDS even with a successor.
        failed = cleanup(m, runner_id=299, name=earlier[0]['name']); failed.update(stage='delete-intent', success=False)
        with self.assertRaisesRegex(RuntimeError, 'did not positively finish'):
            finish.validate_cleanup_receipts(m, cleanups(m) + [failed])

    def test_reused_QEMU_PID_with_distinct_START_is_accepted_identical_identity_holds(self):
        # PIDs recycle across thousands of VMs per boot: identity is (PID, START).
        m = manifest(); w = m['drain_witness']['1']; last = w['vm_history'][0]
        earlier = []
        for index, (start, stop) in enumerate((('25100000', '25200000'), ('25300000', '25400000'))):
            vm = copy.deepcopy(last)
            vm.update(name=f'hound-ci-1-00000000a{index:03x}', start_monotonic=start, stop_monotonic=stop,
                      start_realtime=str(finish.micros(f'2026-10-05T12:2{index + 1}:00+00:00')),
                      stop_realtime=str(finish.micros(f'2026-10-05T12:2{index + 1}:30+00:00')), qemu_pid=last['qemu_pid'])
            earlier.append(vm)
        last.update(start_monotonic='25500000', start_realtime=str(finish.micros('2026-10-05T12:23:00+00:00')))
        w['vm_history'][:0] = earlier
        m['gates']['1']['host_qemu_before_gate'] = None
        sync_first_read(m)
        adopted = [vm for vm in finish.accepted_vms(m) if vm['slot'] == 1]
        self.assertEqual([vm['final_accepted_vm']['qemu_pid'] for vm in adopted], [last['qemu_pid']] * 3)
        self.assertEqual(len({vm['final_accepted_vm']['start_monotonic'] for vm in adopted}), 3)
        same = copy.deepcopy(m); history = same['drain_witness']['1']['vm_history']
        history[0]['stop_monotonic'] = history[0]['start_monotonic']
        history[1]['start_monotonic'] = history[0]['start_monotonic']
        sync_first_read(same)
        with self.assertRaisesRegex(RuntimeError, 'Repeated adopted QEMU process identity'):
            finish.accepted_vms(same)

    def test_successor_proof_is_per_slot_and_never_crosses_controllers(self):
        m = manifest()
        # Slot 2's later VM cannot vouch for slot 1's last VM.
        w2 = m['drain_witness']['2']; later = copy.deepcopy(w2['vm_history'][0])
        later.update(name='hound-ci-2-00000000b000', start_monotonic='31000000', stop_monotonic='32000000',
                     start_realtime=str(finish.micros('2026-10-05T12:29:10+00:00')),
                     stop_realtime=str(finish.micros('2026-10-05T12:29:20+00:00')), qemu_pid=880)
        w2['vm_history'].append(later); w2['latest_vm'].update(name=later['name']); w2['latest_vm_monotonic'] = later['stop_monotonic']
        m['gates']['2']['registration_after_gate'] = None
        records = [cleanup(m, slot) for slot in (2, 3, 4)]
        records[0]['name'] = w2['vm_history'][0]['name']
        records.append(cleanup(m, 2, runner_id=902, name=later['name']))
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, records)
        known = finish.validate_cleanup_receipts(m, records + [cleanup(m)])
        self.assertEqual(known[later['name']], 902)
        # Slot 2's first VM is successor-covered: its own receipt is optional.
        finish.validate_cleanup_receipts(m, [row for row in records if row['id'] != 202] + [cleanup(m)])

    def test_delete_exact_schema_failed_intent_and_identity_disagreement_hold(self):
        m = manifest(); finish.validate_collection(collection(m), m, cleanups(m))
        for key, value in [('slot', True), ('old_pid', 999), ('starttime', '999'), ('id', 999),
                           ('repo', 'other/repo'), ('drain_nonce', 'other'), ('gate_sha256', 'f'*64),
                           ('name', 'hound-ci-1-abcdef012345'), ('stage', 'delete-intent'), ('success', False),
                           ('success', 1), ('returncode', True), ('utc', '2026-10-05T11:55:00Z')]:
            receipt = cleanup(m); receipt[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): finish.validate_cleanup_receipts(m, [receipt])
        receipt = cleanup(m); receipt.update(stage='delete-intent', success=False); receipt.pop('returncode')
        with self.assertRaises(RuntimeError): finish.validate_cleanup_receipts(m, [receipt])
        receipt = cleanup(m); receipt['returncode'] = 1  # Positive root 404 cleanup is permitted.
        finish.validate_collection(collection(m), m, cleanups(m, receipt))

    def test_missing_one_or_all_current_DELETE_receipts_is_unknown_HOLD(self):
        m = manifest(); records = cleanups(m)
        for remaining in ([], records[1:], records[:3]):
            with self.subTest(remaining=remaining), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_cleanup_receipts(m, remaining)
            with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_collection(collection(m), m, remaining)
        finish.validate_cleanup_receipts(m, records)
        # Ordinary GET-only metadata remains possible; it is NOT a certificate.
        finish.validate_collection(collection(m), m)

    def test_before_or_after_positive_gate_registration_always_requires_DELETE(self):
        for key in ('registration', 'registration_after_gate'):
            m = historical_manifest()
            name = m['drain_witness']['1']['vm_history'][-1]['name']
            m['gates']['1'][key] = {'repo': finish.REPO, 'name': name, 'id': 201}
            sync_first_read(m)
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_cleanup_receipts(m, [])
            finish.validate_cleanup_receipts(m, [cleanup(m)])

    def test_distinct_positive_before_after_gate_ID_names_both_need_DELETE(self):
        m = post_gate_manifest()
        m['gates']['1']['registration_after_gate'] = {
            'repo': finish.REPO, 'id': 901, 'name': m['drain_witness']['1']['vm_history'][-1]['name']}
        m['armed'][0]['utc'] = '2026-10-05T12:22:01Z'
        records = cleanups(m)
        records[0] = cleanup(m, name=m['drain_witness']['1']['vm_history'][0]['name'])
        records.append(cleanup(m, runner_id=901))
        finish.validate_collection(collection(m), m, records)
        # 201's VM has a later START in the same process: its DELETE returned.
        finish.validate_collection(collection(m), m, [row for row in records if row['id'] != 201])
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_collection(collection(m), m, [row for row in records if row['id'] != 901])

    def test_historical_pre_gate_erased_requires_both_ordered_root_observations(self):
        m = historical_manifest()
        self.assertEqual(len(finish.accepted_vms(m)), 4)
        self.assertEqual(finish.validate_cleanup_receipts(m, []), {
            vm['name']: None for vm in finish.accepted_vms(m)})
        finish.validate_collection(collection(m), m, [])
        for stop in ('2026-10-05T12:20:30Z', '2026-10-05T12:20:31Z', '2026-10-05T12:20:32Z'):
            current = historical_manifest()
            current['drain_witness']['1']['vm_history'][-1]['stop_realtime'] = str(finish.micros(stop))
            with self.subTest(stop=stop), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_cleanup_receipts(current, [])
        for mutate in (lambda m: m['armed'][0].pop('utc'),
                       lambda m: m['armed'][0].update(utc='2026-10-05T12:20:29Z'),
                       lambda m: m['armed'][0].update(utc='2026-10-05T12:30:01Z'),
                       lambda m: m['gates']['1'].update(observed_utc='2026-10-05T12:19:59Z')):
            current = historical_manifest(); mutate(current)
            with self.assertRaises(RuntimeError): finish.validate_cleanup_receipts(current, [])

    def test_inflight_original_pregate_DELETE_without_captured_return_HOLDS(self):
        m = manifest()
        # It could have called the original immutable DELETE before the wrapper
        # bind; root STOP after the gate + final record absence does NOT prove
        # its missing return receipt. No new API or auto reconciliation here.
        m['gates']['1'].update(registration=None, registration_after_gate=None, host_qemu_before_gate=None)
        sync_first_read(m)
        records = cleanups(m)[1:]
        with self.assertRaisesRegex(RuntimeError, 'requires separately authorized reconciliation'):
            finish.validate_cleanup_receipts(m, records)
        finish.validate_cleanup_receipts(m, cleanups(m))

    def test_post_gate_and_armed_slot_inflight_adopted_VM_need_own_DELETE(self):
        for begin in ('2026-10-05T12:20:30Z', '2026-10-05T12:20:31Z', '2026-10-05T12:21:00Z'):
            m = historical_manifest(); w = m['drain_witness']['1']
            vm = copy.deepcopy(w['vm_history'][0])
            vm.update(name='hound-ci-1-abcdef012345', start_monotonic='31000000', stop_monotonic='35000000',
                      start_realtime=str(finish.micros(begin)), stop_realtime=str(finish.micros('2026-10-05T12:29:00Z')),
                      qemu_pid=901)
            w['vm_history'].append(vm)
            w['latest_vm'].update(name=vm['name'])
            w['latest_vm_monotonic'] = vm['stop_monotonic']
            with self.subTest(begin=begin), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_cleanup_receipts(m, [])
            known = finish.validate_cleanup_receipts(m, [cleanup(m, name=vm['name'], runner_id=901)])
            self.assertEqual(known[vm['name']], 901)
            # The earlier VM stopped before the arm boundary: history only.
            self.assertNotIn(w['vm_history'][0]['name'], known)



    def test_fast_VM_between_registration_reads_is_not_historical_even_if_wall_clock_precedes_END(self):
        m = historical_manifest()
        w = m['drain_witness']['1']
        vm = w['vm_history'][-1]
        # First absence belongs to an earlier VM. This NEW B starts and stops
        # entirely between both registration reads: both reads absent is NOT
        # B's DELETE return proof. END/wall-clock-only exemption was unsound.
        vm.update(start_monotonic='25000001', stop_monotonic='26000000')
        w['latest_vm_monotonic'] = vm['stop_monotonic']
        vm['start_realtime'] = str(finish.micros('2026-10-05T12:20:29.100000Z'))
        vm['stop_realtime'] = str(finish.micros('2026-10-05T12:20:29.500000Z'))
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, [])
        finish.validate_cleanup_receipts(m, [cleanup(m)])

    def test_STOP_precedes_read_but_positive_FIRST_registration_proves_cleanup_still_inflight(self):
        m = historical_manifest(); vm = m['drain_witness']['1']['vm_history'][-1]
        self.assertLess(int(vm['stop_monotonic']), int(m['gates']['1']['observation_started_monotonic_us']))
        m['gates']['1']['registration'] = {'repo': finish.REPO, 'id': 201, 'name': vm['name']}
        sync_first_read(m)
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, [])
        finish.validate_cleanup_receipts(m, [cleanup(m)])
        # NULL first registration is not positive ID, but is STILL not absence
        # and so cannot attest that original serial cleanup already returned.
        m['gates']['1']['registration']['id'] = None
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, [])

    def test_mandatory_PRE_first_read_boundary_missing_invalid_or_equal_STOP_HOLDS(self):
        for mutate in (
            lambda g: g.pop('observation_started_monotonic_us'), lambda g: g.pop('observation_started_utc'),
            lambda g: g.update(observation_started_monotonic_us=25000000),
            lambda g: g.update(observation_started_monotonic_us='0'),
            lambda g: g.update(observation_started_utc='2026-10-05T12:20:31Z'),
            lambda g: g.update(observation_started_utc='2026-10-05T12:19:59Z'),
            lambda g: g.update(observation_started_monotonic_us='24000000'),
        ):
            m = historical_manifest(); mutate(m['gates']['1']); sync_first_read(m)
            with self.subTest(gate=m['gates']['1']), self.assertRaises(RuntimeError):
                finish.validate_cleanup_receipts(m, [])

    def test_NTP_wall_clock_STEP_cannot_turn_current_STOP_into_historical_cleanup(self):
        m = historical_manifest(); vm = m['drain_witness']['1']['vm_history'][-1]
        vm['stop_monotonic'] = '25000000'
        m['drain_witness']['1']['latest_vm_monotonic'] = vm['stop_monotonic']
        # Walltime looks older than snapshot/arm, but monotonic STOP equals the
        # PRE-read boundary, so proof must be mandatory and separately captured.
        with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
            finish.validate_cleanup_receipts(m, [])


    def test_first_read_record_must_be_reviewed_armer_verbatim_post_boundary(self):
        m = historical_manifest()
        self.assertEqual(finish.validate_cleanup_receipts(m, []), {vm['name']: None for vm in finish.accepted_vms(m)})
        for mutate in (lambda f: f.update(operator_sha256='f' * 64), lambda f: f.update(path='/var/lib/hound-ci/slot-2-registration.json'),
                       lambda f: f.update(boot_id='2c0a7d1e-0000-4000-8000-00000000b007'), lambda f: f.update(present=0),
                       lambda f: f.update(present=True), lambda f: f.update(after_monotonic_us='24999999'),
                       lambda f: f.update(extra='x'), lambda f: f.pop('present')):
            current = historical_manifest(); mutate(current['gates']['1']['first_registration_read'])
            with self.subTest(first=current['gates']['1']['first_registration_read']), \
                 self.assertRaisesRegex(RuntimeError, 'first registration read|First registration read'):
                finish.validate_cleanup_receipts(current, [])
        # A positive snapshot can never be relabelled as an absent first read.
        current = manifest(); current['gates']['1']['first_registration_read']['present'] = False
        with self.assertRaisesRegex(RuntimeError, 'First registration read'): finish.accepted_vms(current)
        current = historical_manifest(); current['gates']['1'].pop('first_registration_read')
        with self.assertRaises(RuntimeError): finish.accepted_vms(current)

    def test_STOP_before_boundary_with_record_PRESENT_at_first_read_is_escaped_DELETE_HOLD(self):
        # Original controller's DELETE/unlink was still pending (escaped the
        # gate) when the reviewed first read ran: present -> never historical,
        # whatever the post snapshot shows or whether its id was known.
        for first in ({'id': None}, {'id': 201}):
            for after_gate_absent in (True, False):
                m = historical_manifest(); vm = m['drain_witness']['1']['vm_history'][-1]
                self.assertLess(int(vm['stop_monotonic']), int(m['gates']['1']['observation_started_monotonic_us']))
                m['gates']['1']['registration'] = {'repo': finish.REPO, 'name': vm['name'], **first}
                if not after_gate_absent:
                    m['gates']['1']['registration_after_gate'] = copy.deepcopy(m['gates']['1']['registration'])
                sync_first_read(m)
                with self.subTest(first=first, after_gate_absent=after_gate_absent), \
                     self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                    finish.validate_cleanup_receipts(m, [])
                finish.validate_cleanup_receipts(m, [cleanup(m)])

    def test_any_STOP_after_boundary_without_receipt_HOLDS_even_with_absent_reads_and_negative_wallclock_jump(self):
        for stop_monotonic in ('25000001', '29000000'):
            m = historical_manifest(); vm = m['drain_witness']['1']['vm_history'][-1]
            vm['stop_monotonic'] = stop_monotonic
            m['drain_witness']['1']['latest_vm_monotonic'] = stop_monotonic
            # Negative wall-clock step: realtime STOP looks an hour older than
            # the boundary/snapshot/arm receipts; monotonic order still wins.
            vm['start_realtime'] = str(finish.micros('2026-10-05T11:10:00Z'))
            vm['stop_realtime'] = str(finish.micros('2026-10-05T11:19:00Z'))
            self.assertFalse(m['gates']['1']['first_registration_read']['present'])
            with self.subTest(stop=stop_monotonic), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                finish.validate_cleanup_receipts(m, [])
            finish.validate_cleanup_receipts(m, [cleanup(m)])


class ResponseIdentityTests(unittest.TestCase):
    def test_response_identity_not_request_identity(self):
        api = FakeAPI()
        api.direct[1001] = job(1, job_id=2000)
        with self.assertRaisesRegex(RuntimeError, 'Direct job GET differs'):
            finish.collect(manifest(), api)
        api = FakeAPI(); api.attempts[1]['id'] = 999
        with self.assertRaisesRegex(RuntimeError, 'RESPONSE identity'):
            finish.collect(manifest(), api)

    def test_wrong_runner_id_name_repo_run_attempt_fail(self):
        for mutation in (
            lambda api: api.jobs[1][0].update(runner_id=999),
            lambda api: api.jobs[1][0].update(runner_name='hound-ci-1-abcdef012345'),
            lambda api: api.direct[1001].update(run_id=999),
            lambda api: api.direct[1001].update(run_attempt=2),
            lambda api: api.direct[1001].update(url='https://api.github.com/repos/other/repo/actions/jobs/1001'),
            lambda api: api.runs[0]['repository'].update(full_name='other/repo'),
            lambda api: api.attempts[1].update(run_attempt=True),
        ):
            api = FakeAPI(); mutation(api)
            with self.subTest(api=api), self.assertRaises(RuntimeError):
                finish.collect(manifest(), api)

    def test_completed_positive_status_known_conclusion_valid_time_only(self):
        for key, value in [('status', 'in_progress'), ('status', None), ('conclusion', None),
                           ('conclusion', ''), ('conclusion', 'queued'), ('conclusion', 'new-outcome'),
                           ('conclusion', 'startup_failure'),
                           ('completed_at', None), ('completed_at', '2026-10-05T12:00:00'),
                           ('completed_at', 'invalid'), ('runner_id', 0), ('id', True),
                           ('run_attempt', None)]:
            row = job(1); row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError): finish.response_job(row)
        for outcome in finish.TERMINAL:
            self.assertEqual(finish.response_job(job(1, conclusion=outcome))['conclusion'], outcome)

    def test_job_started_at_is_NOT_a_VM_ordering_constraint(self):
        api = FakeAPI()
        for row in api.jobs[1]: row['started_at'] = '2026-09-01T00:00:00Z'
        api.direct = {row['id']: copy.deepcopy(row) for row in api.jobs[1]}
        finish.validate_collection(finish.collect(manifest(), api), manifest())

    def test_January_2020_completion_cannot_certify_authenticated_2026_VM(self):
        api = FakeAPI(); api.jobs[1][0]['completed_at'] = '2020-01-01T00:00:00Z'
        api.direct[1001] = copy.deepcopy(api.jobs[1][0])
        with self.assertRaisesRegex(RuntimeError, 'authenticated VM START'):
            finish.collect(manifest(), api)
        m = manifest(); report = collection(m)
        report['jobs'][0]['job']['completed_at'] = '2020-01-01T00:00:00Z'
        with self.assertRaisesRegex(RuntimeError, 'authenticated VM START'):
            finish.validate_collection(report, m, cleanups(m))

    def test_completion_metadata_allowance_exact_five_second_bounds(self):
        m = manifest()
        self.assertEqual(finish.ACTIONS_CLOCK_SKEW_SECONDS, 5)
        for timestamp in ('2026-10-05T12:09:55Z', '2026-10-05T12:09:55.000001Z',
                          '2026-10-05T12:10:00Z', '2026-10-05T12:31:04.999999Z',
                          '2026-10-05T12:31:05Z', '2026-10-05T12:31:05+00:00'):
            api = FakeAPI(); api.jobs[1][0]['completed_at'] = timestamp
            api.direct[1001] = copy.deepcopy(api.jobs[1][0])
            with self.subTest(timestamp=timestamp):
                report = finish.collect(m, api)
                finish.validate_collection(report, m, cleanups(m))
        for timestamp in ('2026-10-05T12:09:54.999999Z', '2026-10-05T12:31:05.000001Z',
                          '2025-10-05T12:10:00Z', '2027-10-05T12:31:00Z',
                          '2026-10-05T12:10:00+01:00', '2026-10-05T12:31:00-01:00'):
            api = FakeAPI(); api.jobs[1][0]['completed_at'] = timestamp
            api.direct[1001] = copy.deepcopy(api.jobs[1][0])
            with self.subTest(timestamp=timestamp), self.assertRaises(RuntimeError): finish.collect(m, api)

    def test_whole_second_truncation_compares_exact_authenticated_microseconds(self):
        m = manifest(); vm = m['drain_witness']['1']['vm_history'][0]
        vm['start_realtime'] = str(finish.micros('2026-10-05T12:10:00.999999Z'))
        report = collection(m); report['jobs'][0]['job']['completed_at'] = '2026-10-05T12:09:56Z'
        finish.validate_collection(report, m, cleanups(m))
        report['jobs'][0]['job']['completed_at'] = '2026-10-05T12:09:55Z'
        with self.assertRaises(RuntimeError): finish.validate_collection(report, m, cleanups(m))
        # Upper collection bound is equally exact, not rounded to a new second.
        report = collection(m); report['collected_utc'] = '2026-10-05T12:31:00.000001Z'
        report['jobs'][0]['job']['completed_at'] = '2026-10-05T12:31:05.000001Z'
        finish.validate_collection(report, m, cleanups(m))
        report['jobs'][0]['job']['completed_at'] = '2026-10-05T12:31:05.000002Z'
        with self.assertRaises(RuntimeError): finish.validate_collection(report, m, cleanups(m))

    def test_queued_and_started_at_old_future_missing_remain_free(self):
        for value in ('2020-01-01T00:00:00Z', '2027-01-01T00:00:00Z', None):
            api = FakeAPI()
            for row in api.jobs[1]: row.update(started_at=value, queued_at=value)
            api.direct = {row['id']: copy.deepcopy(row) for row in api.jobs[1]}
            with self.subTest(value=value):
                finish.validate_collection(finish.collect(manifest(), api), manifest(), cleanups(manifest()))

    def test_ambiguous_jobs_no_match_failed_paging_fail(self):
        api = FakeAPI(); duplicate = job(1, job_id=2001)
        api.jobs[1].append(duplicate); api.direct[2001] = duplicate
        with self.assertRaisesRegex(RuntimeError, 'no-match/ambiguous'): finish.collect(manifest(), api)
        api = FakeAPI(); api.jobs[1].pop()
        with self.assertRaisesRegex(RuntimeError, 'no-match/ambiguous'): finish.collect(manifest(), api)
        api = FakeAPI(); api.paged = Mock(side_effect=RuntimeError('incomplete page'))
        with self.assertRaises(RuntimeError): finish.collect(manifest(), api)

    def test_every_prior_run_attempt_is_fully_enumerated(self):
        api = FakeAPI()
        api.runs[0]['run_attempt'] = 2
        api.attempts[2] = copy.deepcopy(api.runs[0])
        api.jobs[2] = []
        report = finish.collect(manifest(), api)
        jobs = [page['route'] for page in report['paging'] if page['key'] == 'jobs']
        self.assertEqual(jobs, [f'repos/{finish.REPO}/actions/runs/500/attempts/{n}/jobs' for n in (1, 2)])
        finish.validate_collection(report, manifest())
        report['paging'].pop()
        with self.assertRaises(RuntimeError): finish.validate_collection(report, manifest())


class PaginationAndPrivilegeTests(unittest.TestCase):
    def api(self, rows):
        api = object.__new__(finish.GitHub)
        api.pages = []; api.calls = 0
        api.get = Mock(side_effect=rows)
        return api

    def test_complete_pages_counts_no_silent_short_page_or_paging_cap(self):
        rows = [{'id': n} for n in range(1, 102)]
        api = self.api([{'total_count': 101, 'jobs': rows[:100]}, {'total_count': 101, 'jobs': rows[100:]}])
        self.assertEqual(len(api.paged('repos/xmit-dev/ultimator/actions/runs/500/attempts/1/jobs', 'jobs')), 101)
        self.assertEqual(api.pages[0]['pages'], 2)
        for payload in (
            [{'total_count': 101, 'jobs': rows[:99]}],
            [{'total_count': 101, 'jobs': rows[:100]}, {'total_count': 102, 'jobs': rows[100:]}],
            [{'total_count': 101, 'jobs': rows[:100]}, {'total_count': 101, 'jobs': [rows[0]]}],
            [{'total_count': 10001, 'jobs': rows[:100]}],
            [{'total_count': 2, 'jobs': [{'id': 1}, {'id': True}]}],
            [{'total_count': 0, 'jobs': [{'id': 1}]}],
            [{'total_count': True, 'jobs': []}],
        ):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.api(payload).paged('repos/xmit-dev/ultimator/actions/runs/500/attempts/1/jobs', 'jobs')
        self.assertEqual(self.api([{'total_count': 0, 'jobs': []}]).paged('route', 'jobs'), [])

    def test_collector_root_forbidden_before_ELF_or_API(self):
        with patch.object(finish.os, 'geteuid', return_value=0), patch.object(finish, 'read_root_bytes') as read:
            with self.assertRaisesRegex(RuntimeError, 'ordinary pcarrier'): finish.GitHub('a' * 64)
            read.assert_not_called()

    def test_collector_original_ELF_no_credentials_reads_no_retry_GET_only(self):
        binary = b'\x7fELFmock-gh'
        with patch.object(finish.os, 'geteuid', return_value=1000), patch.object(finish.os, 'getuid', return_value=1000), \
             patch.object(finish, 'read_root_bytes', return_value=binary) as read:
            api = finish.GitHub(finish.digest(binary))
            read.assert_called_once_with(finish.GH_ELF, 64 * 1024 * 1024)
        with patch.object(finish, 'ordinary_get', return_value=b'{"id":1}') as run:
            self.assertEqual(api.get('repos/xmit-dev/ultimator/actions/jobs/1'), {'id': 1})
            self.assertEqual(run.call_args.args[0], [str(finish.GH_ELF), 'api', '--hostname', 'github.com', '--method', 'GET',
                                                   'repos/xmit-dev/ultimator/actions/jobs/1'])
            self.assertEqual(run.call_args.kwargs, {})
            self.assertEqual(run.call_count, 1)
        with patch.object(finish.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError): api.get('repos/other/repo/actions/jobs/1')
            run.assert_not_called()
        api.calls = finish.MAX_CALLS
        with patch.object(finish.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError): api.get('repos/xmit-dev/ultimator/actions/jobs/1')
            run.assert_not_called()

    def test_certificate_report_exactschema_sources_nonce_manifest_and_paging(self):
        m = manifest()
        for mutate in (
            lambda r: r.update(extra='untrusted'), lambda r: r.update(schema=True),
            lambda r: r.update(operator_uid=0), lambda r: r.update(drain_nonce='another'),
            lambda r: r.update(manifest_sha256='0' * 64),
            lambda r: r['sources'].update(waiter_sha256='0' * 64),
            lambda r: r.update(gh_source='/tmp/gh'), lambda r: r.update(paging=[]),
            lambda r: r.update(runs=[]), lambda r: r.update(request_count=999),
            lambda r: r['jobs'].pop(), lambda r: r['jobs'][0]['job'].update(runner_id=999),
            lambda r: r['jobs'][0]['job'].update(run_id=999),
            lambda r: r['jobs'][0]['job'].update(conclusion='queued'),
            lambda r: r['jobs'][0]['job'].update(completed_at='2027-10-05T00:00:00Z'),
            lambda r: r['paging'].append(copy.deepcopy(r['paging'][0])),
        ):
            report = collection(m); mutate(report)
            with self.subTest(report=report), self.assertRaises(RuntimeError): finish.validate_collection(report, m)

    def test_certificate_requires_root_receipt_and_all_pinned_sources(self):
        m = manifest(); receipt = certificate(m)
        with patch.object(finish, 'validate_operator_source') as validate, \
             patch.object(finish, 'read_public_json', return_value=receipt) as read, \
             patch.object(finish, 'read_cleanup_receipts', return_value=cleanups(m)), \
             patch.object(finish, 'read_capture', return_value=(receipt['collection'], receipt['invocation'])):
            self.assertEqual(finish.validate_certificate(m), receipt)
            self.assertEqual(validate.call_count, 5)
            read.assert_called_once_with(finish.STATE / 'actions-terminal.json')
        with patch.object(finish, 'validate_operator_source'), patch.object(finish, 'read_public_json', side_effect=PermissionError('untrusted file')):
            with self.assertRaises(PermissionError): finish.validate_certificate(m)
        receipt['collection']['jobs'][0]['job']['runner_name'] = 'hound-ci-1-abcdef012345'
        with patch.object(finish, 'validate_operator_source'), patch.object(finish, 'read_public_json', return_value=receipt), \
             patch.object(finish, 'read_capture', return_value=(collection(m), receipt['invocation'])):
            with self.assertRaises(RuntimeError): finish.validate_certificate(m)

    def test_rootcertify_never_calls_API_and_rechecks_before_and_after_receipt(self):
        m = manifest(); report = collection(m); drain = Mock()
        with patch.object(finish.os, 'geteuid', return_value=0), \
             patch.object(finish.os, 'stat', return_value=SimpleNamespace(st_ino=1)), \
             patch.object(finish, '__file__', m['validator_source']), \
             patch.object(finish, 'read_public_json', return_value=m), \
             patch.object(finish.os, 'getuid', return_value=0), \
             patch.object(finish, 'read_capture', return_value=(report, invocation_fixture(m, report))), \
             patch.object(finish, 'read_cleanup_receipts', return_value=cleanups(m)), \
             patch.object(finish, 'validate_operator_source'), \
             patch.object(finish, 'load_source', return_value=drain) as load, \
             patch.object(finish, 'revalidate_final') as fresh, \
             patch.object(finish, 'write_certificate') as write, \
             patch.object(finish, 'validate_certificate') as validate, \
             patch.object(finish, 'GitHub', side_effect=AssertionError('NO root API')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            finish.certify(finish.capture_paths()[2])
            self.assertEqual(fresh.call_count, 2)
            self.assertEqual(load.call_count, 1)
            write.assert_called_once()
            validate.assert_called_once_with(m)
            self.assertIn('no-root-api', output.getvalue())
        with patch.object(finish.os, 'geteuid', return_value=1000), patch.object(finish, 'read_public_json') as read:
            with self.assertRaises(RuntimeError): finish.certify(Path('/anything'))
            read.assert_not_called()

    def test_root_certificate_missing_one_or_all_receipts_holds_despite_final_absence(self):
        m = manifest()
        for records in ([], cleanups(m)[1:]):
            receipt = certificate(m); receipt['cleanup_receipts'] = records
            with patch.object(finish, 'validate_operator_source'), \
                 patch.object(finish, 'read_public_json', return_value=receipt), \
                 patch.object(finish, 'read_cleanup_receipts', return_value=records), \
                 patch.object(finish, 'read_capture', return_value=(receipt['collection'], receipt['invocation'])):
                with self.subTest(records=records), self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                    finish.validate_certificate(m)

    def test_root_certify_missing_cleanup_never_writes_or_enters_hardware_checks(self):
        m = manifest(); report = collection(m)
        for records in ([], cleanups(m)[1:]):
            with patch.object(finish.os, 'geteuid', return_value=0), \
                 patch.object(finish.os, 'stat', return_value=SimpleNamespace(st_ino=1)), \
                 patch.object(finish, '__file__', m['validator_source']), \
                 patch.object(finish, 'read_public_json', return_value=m), \
             patch.object(finish.os, 'getuid', return_value=0), \
             patch.object(finish, 'read_capture', return_value=(report, invocation_fixture(m, report))), \
                 patch.object(finish, 'read_cleanup_receipts', return_value=records), \
                 patch.object(finish, 'validate_operator_source'), \
                 patch.object(finish, 'revalidate_final') as fresh, \
                 patch.object(finish, 'write_certificate') as write, \
                 patch.object(finish, 'GitHub', side_effect=AssertionError('NO root API')):
                with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                    finish.certify(finish.capture_paths()[2])
                fresh.assert_not_called(); write.assert_not_called()




    def test_per_attempt_page_count_cannot_be_zero_or_less_than_its_matched_jobs(self):
        m = manifest()
        for count in (0, 1, 2, 3):
            report = collection(m)
            next(page for page in report['paging'] if page['key'] == 'jobs')['total_count'] = count
            with self.subTest(count=count), self.assertRaisesRegex(RuntimeError, 'Associated job page count'):
                finish.validate_collection(report, m)
        report = collection(m)
        report['paging'].append({'route': f'repos/{finish.REPO}/actions/runs/999/attempts/1/jobs',
                                 'key': 'jobs', 'pages': 1, 'total_count': 4})
        report['request_count'] += 1
        with self.assertRaisesRegex(RuntimeError, 'All scanned run attempts'):
            finish.validate_collection(report, m)

    def test_separate_attempt_zero_page_cannot_borrow_count_from_another_attempt(self):
        api = FakeAPI(); api.runs[0]['run_attempt'] = 2
        api.attempts[2] = copy.deepcopy(api.runs[0]); api.jobs[2] = []
        m = manifest(); report = finish.collect(m, api)
        pages = {page['route']: page for page in report['paging']}
        route1 = f'repos/{finish.REPO}/actions/runs/500/attempts/1/jobs'
        route2 = f'repos/{finish.REPO}/actions/runs/500/attempts/2/jobs'
        pages[route1]['total_count'] = 0
        pages[route2]['total_count'] = 4
        with self.assertRaisesRegex(RuntimeError, 'Associated job page count'):
            finish.validate_collection(report, m)


class RootCaptureSecurityTests(unittest.TestCase):
    def test_manually_promoted_root_JSON_is_not_an_accepted_certification_path(self):
        with patch.object(finish.os, 'geteuid', return_value=0), \
             patch.object(finish, 'read_public_json') as read, patch.object(finish, 'read_capture') as capture:
            with self.assertRaisesRegex(RuntimeError, 'No user-supplied/promoted'):
                finish.certify(Path('/root/manually-chowned-collection.json'))
            read.assert_not_called(); capture.assert_not_called()
        with patch.object(finish.sys, 'argv', ['finish-drain.py', '--certify', '/root/collection.json']), \
             patch('sys.stderr', new_callable=io.StringIO), patch.object(finish, 'certify') as certify:
            with self.assertRaises(SystemExit): finish.main()
            certify.assert_not_called()

    def test_invocation_source_input_nonce_output_exit_PID_and_actual_UID_are_bound(self):
        m = manifest(); report = collection(m); output = finish.canonical(report) + b'\n'
        receipt = invocation_fixture(m, report)
        self.assertEqual(finish.validate_invocation(receipt, receipt['intent'], output, m), report)
        for mutate in (
            lambda r: r.update(extra='promoted'), lambda r: r.update(child_exit=1), lambda r: r.update(child_exit=True),
            lambda r: r.update(child_pid=777), lambda r: r.update(child_pid=True),
            lambda r: r['boundary'].update(uids=[0] * 4), lambda r: r['boundary'].update(groups=[1]),
            lambda r: r['boundary'].update(caps=[0, 0, 1, 0, 0]), lambda r: r['boundary'].update(no_new_privs=False),
            lambda r: r['boundary'].update(executable='/usr/bin/python3'), lambda r: r['boundary'].update(starttime='0'),
            lambda r: r['intent']['execution'].update(producer_sha256='f' * 64),
            lambda r: r['intent']['execution'].update(bootstrap_sha256='f' * 64),
            lambda r: r['intent']['execution'].update(environment_sha256='f' * 64),
            lambda r: r['intent'].update(input_sha256='f' * 64), lambda r: r['intent'].update(input_bytes=1),
            lambda r: r['intent'].update(drain_nonce='another'), lambda r: r['intent'].update(manifest_sha256='f' * 64),
            lambda r: r['intent'].update(invocation_id='not-a-uuid'), lambda r: r.update(argv_sha256='f' * 64),
            lambda r: r.update(ready_fd=r['ack_fd']), lambda r: r.update(output_sha256='f' * 64),
            lambda r: r.update(output_bytes=1), lambda r: r.update(request_count=1),
            lambda r: r.update(response_identity_sha256='f' * 64),
            lambda r: r.update(finished_monotonic_ns='1850000000001'),
            lambda r: r.update(finished_utc='2026-10-05T12:30:00Z'),
        ):
            bad = copy.deepcopy(receipt); mutate(bad)
            with self.subTest(receipt=bad), self.assertRaises(RuntimeError):
                finish.validate_invocation(bad, bad['intent'], output, m)
        for bad_output in (output[:-1], output[:30], b' ' + output, b'x' * (finish.LIMIT + 1)):
            bad = copy.deepcopy(receipt)
            bad.update(output_sha256=finish.digest(bad_output), output_bytes=len(bad_output))
            with self.subTest(output_len=len(bad_output)), self.assertRaises(RuntimeError):
                finish.validate_invocation(bad, bad['intent'], bad_output, m)
        different_intent = copy.deepcopy(receipt['intent']); different_intent['invocation_id'] = str(finish.uuid.uuid4())
        with self.assertRaisesRegex(RuntimeError, 'invocation/exit'):
            finish.validate_invocation(receipt, different_intent, output, m)

    def test_capture_requires_complete_own_three_artifacts_never_user_input(self):
        m = manifest(); report = collection(m); receipt = invocation_fixture(m, report)
        folder, intent_path, output_path, invocation_path = finish.capture_paths()
        def listing(names):
            context = Mock(); context.__enter__ = Mock(return_value=iter(SimpleNamespace(name=n) for n in names))
            context.__exit__ = Mock(return_value=False)
            return context
        with patch.object(finish, 'root_directory'), patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
             patch.object(finish.os, 'scandir', return_value=listing(['intent.json', 'stdout.json', 'invocation.json'])), \
             patch.object(finish, 'read_public_json', side_effect=[receipt['intent'], receipt]) as read, \
             patch.object(finish, 'read_root_bytes', return_value=finish.canonical(report) + b'\n') as raw:
            self.assertEqual(finish.read_capture(m), (report, receipt))
            self.assertEqual([c.args[0] for c in read.call_args_list], [intent_path, invocation_path])
            raw.assert_called_once_with(output_path, finish.LIMIT, 0o600)
        for names in (['stdout.json'], ['intent.json', 'stdout.json'], ['intent.json', 'stdout.json', 'invocation.json', 'user.json']):
            with patch.object(finish, 'root_directory'), patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
                 patch.object(finish.os, 'scandir', return_value=listing(names)), patch.object(finish, 'read_public_json') as read:
                with self.subTest(names=names), self.assertRaisesRegex(RuntimeError, 'Incomplete/foreign'):
                    finish.read_capture(m)
                read.assert_not_called()

    def test_production_launch_exact_fixed_isolated_UID1000_no_carried_credentials(self):
        m = manifest(); report = collection(m); receipt = invocation_fixture(m, report)
        output = finish.canonical(report) + b'\n'; data = finish.canonical(m) + b'\n'
        child = SimpleNamespace(pid=9999, returncode=0, stdin=io.BytesIO(), stdout=io.BytesIO(), kill=Mock(), wait=Mock(return_value=0))
        order = []
        with patch.object(finish, 'root_directory'), patch.object(finish.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_gid=100, pw_dir='/home/pcarrier')), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=0o100555, st_uid=0)), \
             fake_gh_config(order) as target, \
             patch.object(finish.os, 'access', return_value=True), \
             patch.object(finish.os, 'pipe2', side_effect=[(10, 11), (12, 13)]), \
             patch.object(finish.os, 'close') as close, patch.object(finish.os, 'read', return_value=b'COLLECTOR_READY\n'), \
             patch.object(finish.os, 'write', return_value=3) as ack, patch.object(finish.select, 'select', return_value=([10], [], [])), \
             patch.object(finish, 'observe_child', return_value=receipt['boundary']) as observed, \
             patch.object(finish, 'pump_child', return_value=(output, 0)) as pump, \
             patch.object(finish.subprocess, 'Popen', return_value=child) as launch:
            actual_output, actual = finish.run_producer(m, data)
            self.assertEqual(actual_output, output); self.assertEqual(actual['child_exit'], 0)
            self.assertEqual(launch.call_args.args[0], finish.producer_argv(m, 11, 12))
            kwargs = launch.call_args.kwargs
            self.assertEqual(kwargs['cwd'], '/var/empty')
            self.assertEqual(kwargs['env'], finish.producer_environment())
            self.assertEqual(set(kwargs['env']), {'HOME', 'USER', 'LOGNAME', 'LANG', 'PATH', 'GH_TELEMETRY', 'GH_CONFIG_DIR',
                                                  'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                                                  'GH_NO_UPDATE_NOTIFIER', 'GH_PROMPT_DISABLED'})
            # No collector/gh path may resolve under the UID-1000-writable home.
            self.assertFalse(any('/home/' in value for value in kwargs['env'].values()))
            self.assertEqual({kwargs['env'][key] for key in ('HOME', 'GH_CONFIG_DIR', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME',
                                                             'XDG_CACHE_HOME', 'XDG_DATA_HOME')}, {str(finish.GH_CONFIG_DIR)})
            self.assertEqual(order, ['install', ('check', True), 'remove']); self.assertFalse(target.exists())
            self.assertIn(f'--regid={finish.COLLECTOR_GID}', launch.call_args.args[0])
            self.assertTrue(kwargs['close_fds']); self.assertEqual(kwargs['pass_fds'], (11, 12))
            self.assertEqual(kwargs['stderr'], finish.subprocess.DEVNULL)
            observed.assert_called_once_with(child.pid); ack.assert_called_once_with(13, b'GO\n')
            self.assertEqual(pump.call_args.args[:2], (child, data)); child.kill.assert_not_called()
            self.assertEqual(sorted(c.args[0] for c in close.call_args_list), [10, 11, 12, 13])


    def test_actual_UID_GID_group_cap_NNP_executable_boundary_is_parsed_from_own_proc_PID(self):
        gid = str(finish.COLLECTOR_GID).encode()
        good = (b'Name:\tpython3\nUid:\t1000\t1000\t1000\t1000\nGid:\t' + b'\t'.join([gid] * 4) + b'\n'
                b'Groups:\t\nNoNewPrivs:\t1\nCapInh:\t0000000000000000\nCapPrm:\t0000000000000000\n'
                b'CapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nCapAmb:\t0000000000000000\n')
        proc_stat = b'9999 (python3 worker) ' + b' '.join([b'S'] + [b'0'] * 18 + [b'4444'] + [b'0'] * 4)
        for status, passes in ((good, True), (good.replace(b'1000\t1000\t1000\t1000', b'0\t0\t0\t0'), False),
                               (good.replace(b'Groups:\t\n', b'Groups:\t1\n'), False),
                               (good.replace(b'NoNewPrivs:\t1', b'NoNewPrivs:\t0'), False),
                               (good.replace(b'CapEff:\t0000000000000000', b'CapEff:\t0000000000000001'), False)):
            with patch.object(builtins, 'open', side_effect=[io.BytesIO(status), io.BytesIO(proc_stat)]) as opened, \
                 patch.object(finish.os, 'readlink', return_value=finish.python_executable()) as executable:
                if passes:
                    boundary = finish.observe_child(9999)
                    self.assertEqual(boundary['uids'], [1000] * 4); self.assertEqual(boundary['starttime'], '4444')
                    self.assertEqual([c.args[0] for c in opened.call_args_list], ['/proc/9999/status', '/proc/9999/stat'])
                    executable.assert_called_once_with('/proc/9999/exe')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'privilege boundary'): finish.observe_child(9999)
        with patch.object(builtins, 'open', side_effect=[io.BytesIO(good), io.BytesIO(proc_stat)]), \
             patch.object(finish.os, 'readlink', return_value='/usr/bin/python3'):
            with self.assertRaisesRegex(RuntimeError, 'executable/identity'): finish.observe_child(9999)

    def test_actual_privilege_failure_kills_ONLY_owned_collector_before_API_or_input(self):
        m = manifest()
        child = SimpleNamespace(pid=9999, returncode=None, stdin=io.BytesIO(), stdout=io.BytesIO(), kill=Mock(), wait=Mock(return_value=-9))
        order = []
        with fake_gh_config(order) as target, \
             patch.object(finish, 'root_directory'), patch.object(finish.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_gid=100, pw_dir='/home/pcarrier')), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=0o100555, st_uid=0)), patch.object(finish.os, 'access', return_value=True), \
             patch.object(finish.os, 'pipe2', side_effect=[(10, 11), (12, 13)]), patch.object(finish.os, 'close'), \
             patch.object(finish.os, 'read', return_value=b'COLLECTOR_READY\n'), patch.object(finish.os, 'write') as ack, \
             patch.object(finish.select, 'select', return_value=([10], [], [])), \
             patch.object(finish, 'observe_child', side_effect=RuntimeError('Actual collector privilege boundary invalid')), \
             patch.object(finish, 'pump_child') as pump, patch.object(finish.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(RuntimeError, 'privilege boundary'): finish.run_producer(m, b'{}\n')
            child.kill.assert_called_once_with(); pump.assert_not_called(); ack.assert_not_called()
            self.assertEqual(order, ['install', ('check', True), 'remove'])  # Copy never outlives the capture.
            self.assertFalse(target.exists())

    def test_root_capture_feeds_canonical_root_manifest_and_preserves_raw_stdout(self):
        m = manifest(); report = collection(m); mocked = invocation_fixture(m, report)
        output = finish.canonical(report) + b'\n'
        actual = {key: mocked[key] for key in ('child_pid', 'child_exit', 'boundary', 'ready_fd', 'ack_fd', 'argv_sha256')}
        folder, intent_path, output_path, invocation_path = finish.capture_paths()
        with patch.object(finish, 'read_public_json', return_value=m), patch.object(finish, 'root_operator') as operator, \
             patch.object(finish, 'root_directory'), patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
             patch.object(finish.os.path, 'lexists', return_value=False), patch.object(Path, 'mkdir'), \
             patch.object(finish.os, 'chmod'), patch.object(finish.os, 'chown'), \
             patch.object(finish, 'new_capture_intent', return_value=mocked['intent']), \
             patch.object(finish, 'run_producer', return_value=(output, actual)) as run, \
             patch.object(finish.time, 'monotonic_ns', return_value=81000000000), \
             patch.object(finish, 'write_exclusive') as write, patch.object(finish, 'read_capture') as read, \
             patch.object(finish, 'GitHub', side_effect=AssertionError('NO root API')), \
             patch('sys.stdout', new_callable=io.StringIO):
            finish.capture()
            operator.assert_called_once_with(m)
            run.assert_called_once_with(m, finish.canonical(m) + b'\n')
            self.assertEqual(write.call_args_list[1].args, (output_path, output))
            self.assertEqual(write.call_args_list[0].args[0], intent_path)
            self.assertEqual(write.call_args_list[2].args[0], invocation_path)
            receipt = finish.decode(write.call_args_list[2].args[1])
            self.assertEqual(receipt['child_pid'], actual['child_pid'])
            self.assertEqual(receipt['boundary'], actual['boundary']); self.assertEqual(receipt['child_exit'], 0)
            read.assert_called_once_with(m)

    def test_bootstrap_hashes_and_compiles_same_authenticated_source_bytes_no_PYC(self):
        source = b'PUBLIC_TEST_SOURCE = 1\n'
        meta = SimpleNamespace(st_mode=0o100444, st_uid=0, st_gid=0, st_size=len(source),
                               st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
        code = compile(finish.COLLECTOR_BOOTSTRAP, '<fixed-bootstrap>', 'exec')
        for sha, passes in ((finish.digest(source), True), ('f' * 64, False)):
            with patch.object(finish.sys, 'argv', ['-c', '/nix/store/exact-source.py', sha, '11', '12']), \
                 patch.object(finish.os, 'getuid', return_value=1000), patch.object(finish.os, 'geteuid', return_value=1000), \
                 patch.object(finish.os, 'getgid', return_value=finish.COLLECTOR_GID), patch.object(finish.os, 'getegid', return_value=finish.COLLECTOR_GID), \
                 patch.object(finish.os, 'getgroups', return_value=[]), patch.object(finish.os, 'open', return_value=99), \
                 patch.object(finish.os, 'fstat', return_value=meta), patch.object(finish.os, 'fdopen', return_value=io.BytesIO(source)), \
                 patch.object(finish.os, 'close'), patch.object(finish.os, 'write') as ready, patch.object(finish.os, 'read', return_value=b'GO\n'), \
                 patch.object(builtins, 'compile', wraps=compile) as compiled:
                if passes:
                    exec(code, {})
                    compiled.assert_called_once_with(source, '/nix/store/exact-source.py', 'exec')
                    ready.assert_called_once_with(11, b'COLLECTOR_READY\n')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'authenticated source'): exec(code, {})
                    compiled.assert_not_called(); ready.assert_not_called()



    def test_ordinary_GET_ROOT_or_route_escape_forbidden_before_API_launch(self):
        with patch.object(finish.os, 'getuid', return_value=0), patch.object(finish.os, 'geteuid', return_value=0), \
             patch.object(finish.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'never run as root'): finish.ordinary_get(['gh', 'api'])
            launch.assert_not_called()
        api = object.__new__(finish.GitHub); api.calls = 0
        for suffix in ('../admin', 'runs/1/../../secrets', 'runs?per_page=100&page=1&other=1', 'jobs/1/delete'):
            with patch.object(finish, 'ordinary_get') as get:
                with self.subTest(suffix=suffix), self.assertRaisesRegex(RuntimeError, 'GET scope'):
                    api.get(f'repos/{finish.REPO}/actions/{suffix}')
                get.assert_not_called()

    def test_pidfd_completion_is_blocking_event_not_POSIX_wait_timeout_poll(self):
        child = SimpleNamespace(pid=9999, wait=Mock(return_value=0))
        with patch.object(finish.time, 'monotonic', return_value=10), \
             patch.object(finish.os, 'pidfd_open', return_value=44) as opened, patch.object(finish.os, 'close') as closed, \
             patch.object(finish.select, 'select', return_value=([44], [], [])) as watch:
            self.assertEqual(finish.wait_child_event(child, 25), 0)
            opened.assert_called_once_with(child.pid); watch.assert_called_once_with([44], [], [], 15)
            child.wait.assert_called_once_with(); closed.assert_called_once_with(44)
        with patch.object(finish.time, 'monotonic', return_value=10), patch.object(finish.os, 'pidfd_open', return_value=44), \
             patch.object(finish.os, 'close'), patch.object(finish.select, 'select', return_value=([], [], [])):
            with self.assertRaisesRegex(RuntimeError, 'timeout'): finish.wait_child_event(child, 25)

    def test_ordinary_GET_response_is_bounded_while_reading_no_stderr_env_or_retry(self):
        for chunks, exit_code, passes in (([b'{"id":1}', b''], 0, True), ([b'12345', b'6789'], 0, False),
                                         ([b'{"id":1}', b''], 1, False), ([b''], 0, False)):
            child = SimpleNamespace(pid=9999, returncode=exit_code, stdout=Mock(), kill=Mock(), wait=Mock())
            child.stdout.fileno.return_value = 66
            with patch.object(finish, 'GET_LIMIT', 8), patch.object(finish.subprocess, 'Popen', return_value=child) as launch, \
                 patch.object(finish.select, 'select', return_value=([66], [], [])), \
                 patch.object(finish.os, 'read', side_effect=chunks), patch.object(finish, 'wait_child_event', return_value=exit_code):
                if passes:
                    self.assertEqual(finish.ordinary_get(['exact-gh', 'GET']), b'{"id":1}')
                else:
                    with self.assertRaises(RuntimeError): finish.ordinary_get(['exact-gh', 'GET'])
                launch.assert_called_once_with(['exact-gh', 'GET'], stdin=finish.subprocess.DEVNULL,
                                               stdout=finish.subprocess.PIPE, stderr=finish.subprocess.DEVNULL, close_fds=True)
                child.stdout.close.assert_called_once_with()

    def test_root_pipe_capture_reads_bounded_normalized_stdout_and_checks_actual_exit(self):
        class Stream:
            def __init__(self, fd): self.fd = fd; self.closed = False
            def fileno(self): return self.fd
            def close(self): self.closed = True
        class Watch:
            def __init__(self): self.keys = {}; self.steps = ['input', 'output', 'output']
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def register(self, stream, event, kind):
                self.keys[kind] = SimpleNamespace(fd=stream.fileno(), fileobj=stream, data=kind)
            def unregister(self, stream):
                for kind, key in list(self.keys.items()):
                    if key.fileobj == stream: del self.keys[kind]
            def get_map(self): return self.keys
            def select(self, timeout): return [(self.keys[self.steps.pop(0)], 1)]
        for output, exit_code, passes in ((b'12345678', 0, True), (b'123456789', 0, False),
                                        (b'12345678', 1, False), (b'', 0, False)):
            child = SimpleNamespace(pid=9999, stdin=Stream(33), stdout=Stream(34))
            with patch.object(finish, 'LIMIT', 8), patch.object(finish.selectors, 'DefaultSelector', side_effect=Watch), \
                 patch.object(finish.os, 'set_blocking'), patch.object(finish.os, 'write', return_value=3) as write, \
                 patch.object(finish.os, 'read', side_effect=[output, b'']), \
                 patch.object(finish, 'wait_child_event', return_value=exit_code) as waited, \
                 patch.object(finish.time, 'monotonic', return_value=10):
                if passes:
                    self.assertEqual(finish.pump_child(child, b'{}\n', 25), (output, 0))
                    write.assert_called_once_with(33, b'{}\n'); waited.assert_called_once_with(child, 25)
                    self.assertTrue(child.stdin.closed and child.stdout.closed)
                else:
                    with self.assertRaises(RuntimeError): finish.pump_child(child, b'{}\n', 25)

    def test_root_entry_requires_isolated_no_bytecode_flags(self):
        for flags in (SimpleNamespace(isolated=False, dont_write_bytecode=True),
                      SimpleNamespace(isolated=True, dont_write_bytecode=False)):
            with patch.object(finish.sys, 'flags', flags), patch.object(finish, 'validate_operator_source') as validate:
                with self.assertRaisesRegex(RuntimeError, 'fixed Python -I -B'): finish.root_operator(manifest())
                validate.assert_not_called()


class FreshRevalidationTests(unittest.TestCase):
    def harness(self, m):
        drain = SimpleNamespace(STATE=finish.STATE,
            properties=Mock(side_effect=lambda slot: {'MainPID': '0', 'Restart': 'no', 'ControlGroup': '', 'InvocationID': invocation(slot)}),
            starttime=Mock(), public_registration=Mock(return_value=None))
        waiter = SimpleNamespace(STATE=finish.STATE, validate_manifest=Mock())
        waiter.seed_witness = Mock(side_effect=lambda entry, _: real_waiter.seed_witness(entry, m))
        def replay(_, entries, state):
            for entry in entries: state[entry['slot']].update(copy.deepcopy(m['drain_witness'][str(entry['slot'])]))
        waiter.replay = Mock(side_effect=replay)
        waiter.registration_witness = Mock(return_value={'kind': 'public-record-absent'})
        waiter.CgroupWatch = Mock(side_effect=lambda _: SimpleNamespace(sample=Mock(return_value=None), close=Mock()))
        return drain, waiter

    def run_fresh(self, m, drain, waiter):
        with patch.object(finish, 'validate_operator_source'), patch.object(finish, 'read_public_json', return_value=m), \
             patch.object(finish, 'load_source', return_value=waiter), \
             patch.object(finish, 'read_cleanup_receipts', return_value=cleanups(m)), \
             patch.object(finish, 'read_capture', return_value=(collection(m), certificate(m)['invocation'])), \
             patch.object(finish, 'read_activation_journal', return_value=getattr(drain, 'journal', None)), \
             patch.object(finish.os, 'pidfd_open', side_effect=ProcessLookupError):
            return finish.revalidate_final(drain, m)

    def test_fresh_all_four_pidfd_actual_properties_subtree_cleanup_and_replay(self):
        m = manifest(); drain, waiter = self.harness(m)
        self.assertEqual(len(self.run_fresh(m, drain, waiter)), 4)
        self.assertEqual(drain.properties.call_count, 4)
        self.assertEqual(drain.public_registration.call_count, 4)
        waiter.replay.assert_called_once()
        self.assertEqual(waiter.CgroupWatch.call_count, 4)

    def test_actual_replacement_hold_controlgroup_or_populated_descendant_fail(self):
        for values in ({'MainPID': '999', 'Restart': 'no', 'ControlGroup': ''},
                       {'MainPID': '0', 'Restart': 'always', 'ControlGroup': ''},
                       {'MainPID': '0', 'Restart': 'no', 'ControlGroup': '/another'}):
            m = manifest(); drain, waiter = self.harness(m)
            drain.properties = Mock(return_value=values)
            with self.subTest(values=values), self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)
        m = manifest(); drain, waiter = self.harness(m)
        waiter.CgroupWatch = Mock(return_value=SimpleNamespace(sample=Mock(return_value=True), close=Mock()))
        with self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)

    def test_old_process_running_pid_reused_and_unknown_exit_not_certified(self):
        drain = SimpleNamespace(starttime=Mock(return_value='1001'))
        entry = manifest()['controllers'][0]
        with patch.object(finish.os, 'pidfd_open', return_value=123), patch.object(finish.os, 'close'), \
             patch.object(finish.select, 'select', return_value=([], [], [])):
            with self.assertRaisesRegex(RuntimeError, 'not exited'): finish.old_process_exited(drain, entry)
        drain.starttime.return_value = '999'
        with patch.object(finish.os, 'pidfd_open', return_value=123), patch.object(finish.os, 'close'):
            with self.assertRaisesRegex(RuntimeError, 'reused'): finish.old_process_exited(drain, entry)
        with patch.object(finish.os, 'pidfd_open', side_effect=PermissionError):
            with self.assertRaises(PermissionError): finish.old_process_exited(drain, entry)
        with patch.object(finish.os, 'pidfd_open', side_effect=ProcessLookupError): finish.old_process_exited(drain, entry)

    def test_fresh_later_B_root_start_prevents_A_certificate(self):
        m = manifest(); drain, waiter = self.harness(m)
        def replay(_, entries, current):
            current[1]['latest_vm'] = {'kind': 'started', 'name': 'hound-ci-1-abcdef012345'}
        waiter.replay.side_effect = replay
        with self.assertRaisesRegex(RuntimeError, 'Latest root VM'): self.run_fresh(m, drain, waiter)

    def test_registration_absence_or_exact_blocked_null_receipt_fresh(self):
        m = manifest(); drain, waiter = self.harness(m)
        waiter.registration_witness.side_effect = RuntimeError('uncertain positive registration')
        with self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)
        m = manifest(); drain, waiter = self.harness(m)
        waiter.registration_witness.return_value = {'kind': 'exact-local-post-blocked', 'name': 'different'}
        with self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)

    def test_removed_cgroup_cannot_be_just_a_cached_boolean(self):
        m = manifest(); m['drain_witness']['1']['cgroup_removed'] = False
        drain, waiter = self.harness(m)
        with self.assertRaisesRegex(RuntimeError, 'identity-bound manager'): self.run_fresh(m, drain, waiter)
        m = manifest(); drain, waiter = self.harness(m)
        waiter.replay.side_effect = lambda _, __, state: state[1].update(manager_monotonic='99')
        with self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)

    def test_only_reviewed_activation_transition_can_release_loaded_holds(self):
        m = manifest(); drain, waiter = self.harness(m)
        drain.expected_restart = {slot: 'always' for slot in finish.SLOTS}
        drain.properties = Mock(side_effect=lambda slot: {'MainPID': '0', 'Restart': 'always', 'ControlGroup': '', 'InvocationID': invocation(slot)})
        drain.activation_phase = 'start-anchor'
        self.assertEqual(len(self.run_fresh(m, drain, waiter)), 4)
        drain.activation_phase = 'unapproved-remove-hold'
        with self.assertRaises(RuntimeError): self.run_fresh(m, drain, waiter)

    def test_partial_or_unknown_expected_restart_policy_never_releases_holds(self):
        for policy, phase in [({1: 'always', 2: 'no', 3: 'no', 4: 'no'}, 'start-anchor'),
                              ({1: 'no'}, 'hold-remove'), ({slot: 'always' for slot in finish.SLOTS}, 'reload-final'),
                              ({slot: 'always' for slot in finish.SLOTS}, 'start-only-four'),
                              ({slot: 'on-failure' for slot in finish.SLOTS}, 'start-anchor')]:
            m = manifest(); drain, waiter = self.harness(m)
            drain.expected_restart, drain.activation_phase = policy, phase
            with self.subTest(policy=policy, phase=phase), self.assertRaises(RuntimeError):
                self.run_fresh(m, drain, waiter)
            drain.properties.assert_not_called()

    def transition(self, m, started=(1,), intents=()):
        """Activation-context drain + root journal: `started` tracked, `intents` intent-only."""
        drain, waiter = self.harness(m)
        drain.activation_phase = 'start-anchor'
        drain.activation_transition_api = finish.ACTIVATION_TRANSITION_API
        drain.expected_restart = {slot: 'always' for slot in finish.SLOTS}
        starts, new = {}, {}
        for slot in sorted(set(started) | set(intents)):
            unit, request, source = finish.expected_start_record(m['controllers'][slot - 1])
            argv = [f'/nix/store/new-hound-ci/bin/hound-ci', 'worker', '--slot', str(slot), '--repo', finish.REPO,
                    '--guest', '/nix/store/new-guest.sh', '--image', 'base-cache-v2.qcow2']
            record = {'slot': slot, 'unit': unit, 'request': request, 'source': source, 'argv': argv,
                      'image': {'path': finish.CANDIDATE, 'sha256': finish.CANDIDATE_SHA},
                      'pre_start': {'main_pid': '0', 'invocation_id': invocation(slot), 'restart': 'always',
                                    'original_cgroup': 'removed', 'validation_sha256': 'a' * 64},
                      'intent_utc': '2026-10-05T12:30:30+00:00', 'stage': 'start-intent', 'result': None}
            if slot in started:
                record['stage'] = 'started'
                record['result'] = {'returncode': 0, 'job': {'type': 'start', 'mode': 'fail', 'result': 'done'},
                                    'invocation_id': f'{slot + 10:x}' * 32, 'pid': 500 + slot, 'starttime': str(9000 + slot),
                                    'control_group': f'/hound.slice/hound-ci.slice/{unit}', 'utc': '2026-10-05T12:30:31+00:00'}
                new[slot] = {'pid': 500 + slot, 'starttime': str(9000 + slot), 'control_group': record['result']['control_group'],
                             'argv': argv, 'unit': unit}
            starts[str(slot)] = record
        drain.activation_new_controllers = new
        drain.journal = {'schema': 2, 'manifest': m, 'validator_source': m['validator_source'],
                         'validator_sha256': m['validator_sha256'], 'candidate_sha256': finish.CANDIDATE_SHA,
                         'slot_starts': starts}
        live = {}
        def properties(slot):
            if slot in new:
                return {'MainPID': str(new[slot]['pid']), 'Restart': 'always', 'ControlGroup': new[slot]['control_group'],
                        'InvocationID': starts[str(slot)]['result']['invocation_id'], **live.get(slot, {})}
            return {'MainPID': '0', 'Restart': 'always', 'ControlGroup': '', 'InvocationID': invocation(slot), **live.get(slot, {})}
        drain.properties = Mock(side_effect=properties)
        drain.starttime = Mock(side_effect=lambda pid: str(9000 + pid - 500))
        return drain, waiter, live

    def run_transition(self, m, drain, waiter, cgroup=None):
        def membership(pid):
            return cgroup if cgroup is not None else f'0::/hound.slice/hound-ci.slice/hound-ci-{pid - 500}.service\n'
        with patch.object(finish, 'process_cgroup', side_effect=membership):
            return self.run_fresh(m, drain, waiter)

    def test_tracked_transition_moves_only_root_recorded_started_slots(self):
        m = manifest()
        for started in ((1,), (1, 2), (1, 2, 3, 4)):
            drain, waiter, _ = self.transition(m, started)
            with self.subTest(started=started): self.assertEqual(len(self.run_transition(m, drain, waiter)), 4)
        # A slot with ONLY a durable intent stays strict and passes while it is
        # still stopped under its ORIGINAL invocation (start failed/not run).
        drain, waiter, _ = self.transition(m, (1,), intents=(2,))
        self.assertEqual(len(self.run_transition(m, drain, waiter)), 4)

    def test_partial_unknown_or_foreign_transitions_HOLD_never_rollback(self):
        m = manifest()
        cases = []
        # Intent-only slot which DID start (new invocation / running PID): partial.
        cases.append(('partial-started', lambda d, l: l.update({2: {'InvocationID': 'f' * 32}}), (1,), (2,), 'ORIGINAL invocation'))
        cases.append(('partial-running', lambda d, l: l.update({2: {'MainPID': '777'}}), (1,), (2,), 'MainPID/hold drift'))
        # Unknown start of an unrecorded slot (manual/dependency/auto restart).
        cases.append(('unknown-start', lambda d, l: l.update({3: {'InvocationID': 'f' * 32}}), (1,), (), 'ORIGINAL invocation'))
        # Tracked view disagrees with root journal / journal lacks the record.
        cases.append(('view-pid', lambda d, l: d.activation_new_controllers[1].update(pid=999), (1,), (), 'root-durable start result'))
        cases.append(('view-extra', lambda d, l: d.activation_new_controllers.update({2: dict(d.activation_new_controllers[1], unit='hound-ci-2.service')}), (1,), (), 'only a partial|lacks a root-durable'))
        cases.append(('journal-missing', lambda d, l: d.journal['slot_starts'].pop('1'), (1,), (), 'lacks a root-durable'))
        cases.append(('journal-absent', lambda d, l: setattr(d, 'journal', None), (1,), (), 'without a root-durable'))
        cases.append(('journal-other-manifest', lambda d, l: d.journal.update(manifest={**m, 'drain_nonce': '2b123456-1234-4123-8123-123456789abc'}), (1,), (), 'not bound'))
        cases.append(('journal-foreign-slot', lambda d, l: d.journal['slot_starts'].update({'5': {}}), (1,), (), 'Foreign'))
        cases.append(('same-invocation', lambda d, l: d.journal['slot_starts']['1']['result'].update(invocation_id=invocation(1)), (1,), (), 'NEW systemd invocation'))
        cases.append(('failed-job', lambda d, l: d.journal['slot_starts']['1']['result'].update(job={'type': 'start', 'mode': 'replace', 'result': 'done'}), (1,), (), 'fail mode'))
        cases.append(('wrong-image', lambda d, l: d.journal['slot_starts']['1'].update(image={'path': '/var/lib/hound-ci/base.qcow2', 'sha256': finish.CANDIDATE_SHA}), (1,), (), 'image proof'))
        cases.append(('wrong-source', lambda d, l: d.journal['slot_starts']['1'].update(source='/nix/store/other/hound-ci-1.service'), (1,), (), 'image proof'))
        cases.append(('pre-start-not-original', lambda d, l: d.journal['slot_starts']['1']['pre_start'].update(invocation_id='f' * 32), (1,), (), 'pre-start'))
        cases.append(('intent-with-result', lambda d, l: d.journal['slot_starts']['1'].update(stage='start-intent'), (1,), (), 'partial result'))
        # Tracked NEW controller drifted: auto-restart/replacement or PID reuse.
        cases.append(('new-restarted', lambda d, l: l.update({1: {'InvocationID': 'f' * 32}}), (1,), (), 'live MainPID/InvocationID'))
        cases.append(('new-pid', lambda d, l: l.update({1: {'MainPID': '888'}}), (1,), (), 'live MainPID/InvocationID'))
        cases.append(('new-reused', lambda d, l: setattr(d, 'starttime', Mock(return_value='1')), (1,), (), 'reused/replaced'))
        cases.append(('no-api', lambda d, l: setattr(d, 'activation_transition_api', None), (1,), (), 'transition API'))
        cases.append(('held-phase', lambda d, l: setattr(d, 'activation_phase', 'hold-remove'), (1,), (), 'release|hold'))
        for label, mutate, started, intents, message in cases:
            drain, waiter, live = self.transition(m, started, intents)
            mutate(drain, live)
            with self.subTest(case=label), self.assertRaisesRegex(RuntimeError, message):
                self.run_transition(m, drain, waiter)

    def test_tracked_slot_requires_exact_kernel_cgroup_and_fresh_registration(self):
        m = manifest()
        drain, waiter, _ = self.transition(m)
        with self.assertRaisesRegex(RuntimeError, 'exact unit cgroup'):
            self.run_transition(m, drain, waiter, cgroup='0::/hound.slice/hound-ci.slice/hound-ci-2.service\n')
        old_name = m['drain_witness']['1']['vm_history'][-1]['name']
        for record, ok in (({'repo': finish.REPO, 'id': None, 'name': 'hound-ci-1-feedfacecafe'}, True),
                           ({'repo': finish.REPO, 'id': 7, 'name': 'hound-ci-1-feedfacecafe'}, True),
                           ({'repo': finish.REPO, 'id': 7, 'name': old_name}, False),
                           ({'repo': finish.REPO, 'id': None, 'name': 'hound-ci-2-feedfacecafe'}, False)):
            drain, waiter, _ = self.transition(m)
            drain.public_registration = Mock(side_effect=lambda slot, record=record: record if slot == 1 else None)
            with self.subTest(record=record):
                if ok: self.assertEqual(len(self.run_transition(m, drain, waiter)), 4)
                else:
                    with self.assertRaises(RuntimeError): self.run_transition(m, drain, waiter)

    def test_certification_context_never_accepts_tracked_controllers(self):
        m = manifest(); drain, waiter, _ = self.transition(m)
        del drain.activation_phase
        drain.expected_restart = {slot: 'no' for slot in finish.SLOTS}
        with self.assertRaisesRegex(RuntimeError, 'outside activation'): self.run_transition(m, drain, waiter)

    def test_missing_pinned_waiter_history_contract_fails_closed(self):
        m = manifest(); drain, waiter = self.harness(m)
        del waiter.seed_witness
        with self.assertRaisesRegex(RuntimeError, 'replay contract missing'):
            self.run_fresh(m, drain, waiter)
        m = manifest(); drain, waiter = self.harness(m)
        waiter.validate_manifest.side_effect = RuntimeError('old source could forward serial data')
        with self.assertRaisesRegex(RuntimeError, 'forward serial'):
            self.run_fresh(m, drain, waiter)
        waiter.replay.assert_not_called()

    def test_source_import_never_uses_unpinned_sibling_bytecode(self):
        source = b'STATE = __import__("pathlib").Path("/var/lib/hound-ci/rollout-cache-v2-20261005")\n'
        with patch.object(finish, 'validate_operator_source', return_value=source) as checked:
            loaded = finish.load_source('/nix/store/mock-source.py', 'a' * 64, 'mock')
            self.assertEqual(loaded.STATE, finish.STATE)
            checked.assert_called_once_with('/nix/store/mock-source.py', 'a' * 64)
        with patch.object(finish, 'validate_operator_source', return_value=b'STATE="wrong"\n'):
            with self.assertRaises(RuntimeError): finish.load_source('/nix/store/mock-source.py', 'a' * 64, 'mock')


class ActualSourceIntegrationTests(unittest.TestCase):
    """Real seed/replay/registration API and activation DrainView, mocked I/O ONLY."""
    def rows(self, m):
        rows = []
        for e in m['controllers']:
            w = m['drain_witness'][str(e['slot'])]
            for vm in w['vm_history']:
                base = {'_PID': str(e['pid']), '_UID': '0', '_SYSTEMD_UNIT': f'hound-ci-{e["slot"]}.service',
                        '_BOOT_ID': BOOT_HEX, '_SYSTEMD_INVOCATION_ID': e['invocation_id']}
                messages = [
                    (vm['start_monotonic'], vm['start_realtime'],
                     f'HOUND_CI slot={e["slot"]} name={vm["name"]} repo={finish.REPO} disposable VM started'),
                    (str(int(vm['start_monotonic']) + 1), str(int(vm['start_realtime']) + 1),
                     f'HOUND_CI QEMU_SECURITY_VERIFIED pid={vm["qemu_pid"]} uid={e["qemu_uid"]} gid={e["qemu_gid"]} CapInh/Prm/Eff/Bnd/Amb=0 NNP=1'),
                    (vm['stop_monotonic'], vm['stop_realtime'],
                     f'HOUND_CI slot={e["slot"]} VM stopped; preflight=False; runner completed=False; erasing disk')]
                for clock, realtime, text in messages:
                    rows.append({**base, '__CURSOR': f'root-{e["slot"]}-{clock}', '__MONOTONIC_TIMESTAMP': clock,
                                 '__REALTIME_TIMESTAMP': realtime, 'MESSAGE': text})
            rows.append({'_PID': '1', '_UID': '0', '_COMM': 'systemd', 'UNIT': f'hound-ci-{e["slot"]}.service',
                         '_BOOT_ID': BOOT_HEX, 'INVOCATION_ID': e['invocation_id'],
                         'MESSAGE_ID': w['manager_terminal'], '__CURSOR': f'manager-{e["slot"]}',
                         '__MONOTONIC_TIMESTAMP': w['manager_monotonic'],
                         '__REALTIME_TIMESTAMP': str(finish.micros(m['drained_utc']))})
        return sorted(rows, key=lambda row: int(row['__MONOTONIC_TIMESTAMP']))

    def run_real(self, m, rows=None, phase='preflight', restart='no', registration=None, blocked=None):
        operator = SimpleNamespace(STATE=finish.STATE, starttime=Mock())
        drain = real_activation.DrainView(operator)
        drain.activation_phase = phase
        drain.expected_restart = {slot: restart for slot in finish.SLOTS}
        rows = self.rows(m) if rows is None else rows
        data = b''.join(finish.canonical(row) + b'\n' for row in rows)
        def process(*args, **kwargs):
            return SimpleNamespace(stdout=io.BytesIO(data), wait=Mock(return_value=0), poll=Mock(return_value=0))
        def public(path, *args, **kwargs):
            if path == finish.STATE / 'manifest.json': return copy.deepcopy(m)
            if path.name.startswith('blocked-jit-'): return copy.deepcopy(blocked)
            if path == finish.STATE / 'actions-terminal.json': return certificate(m)
            raise AssertionError(f'Unexpected public read: {path}')
        journal = getattr(self, 'journal', None)
        with patch.object(finish, 'validate_operator_source'), patch.object(finish, 'load_source', return_value=real_waiter), \
             patch.object(finish, 'read_public_json', side_effect=public), \
             patch.object(finish, 'read_cleanup_receipts', return_value=cleanups(m)), \
             patch.object(finish, 'read_capture', return_value=(collection(m), certificate(m)['invocation'])), \
             patch.object(finish.os, 'pidfd_open', side_effect=ProcessLookupError), \
             patch.object(real_waiter.os, 'sysconf', return_value=100), \
             patch.object(real_waiter.subprocess, 'Popen', side_effect=process) as spawn, \
             patch.object(real_waiter, 'CgroupWatch', side_effect=lambda _: SimpleNamespace(sample=lambda: None, close=lambda: None)), \
             patch.object(finish, 'read_activation_journal', return_value=journal), \
             patch.object(real_activation, 'properties', side_effect=lambda name: {'MainPID': '0', 'Restart': restart, 'ControlGroup': '',
                                                                                    'InvocationID': invocation(int(name.split('-')[2].split('.')[0]))}), \
             patch.object(real_activation, 'read_public_json', side_effect=lambda _: registration) as root_registration:
            finish.validate_certificate(m)
            result = finish.revalidate_final(drain, m)
            self.assertEqual(root_registration.call_count, 4)
            self.assertIn('--boot', spawn.call_args.args[0])
            self.assertIn('--no-tail', spawn.call_args.args[0])
            self.assertFalse(any(arg.startswith('--since') for arg in spawn.call_args.args[0]))
            return result

    def test_real_waiter_empty_seed_full_boot_replay_matches_actual_manifest(self):
        m = manifest(); original = copy.deepcopy(m)
        for e in m['controllers']:
            seed = real_waiter.seed_witness(e, m)
            self.assertEqual(seed['vm_history'], [])
            self.assertIsNone(seed['latest_vm'])
            self.assertFalse(seed['drained'])
        self.assertEqual(len(self.run_real(m)), 4)
        self.assertEqual(m, original)

    def test_real_activation_DrainView_all_loaded_hold_transition_phases(self):
        m = manifest()
        for phase in ('preflight', 'validated-all-four-stopped', 'root-namespace-create', 'gc-root-create',
                      'unit-link-replace', 'reload-new-held', 'hold-remove', 'reload-final'):
            with self.subTest(phase=phase): self.assertEqual(len(self.run_real(m, phase=phase)), 4)
            with self.subTest(phase=phase, premature=True), self.assertRaises(RuntimeError):
                self.run_real(m, phase=phase, restart='always')
        for phase in sorted(finish.RELEASE_PHASES):
            with self.subTest(release=phase):
                self.assertEqual(len(self.run_real(m, phase=phase, restart='always')), 4)
                with self.assertRaises(RuntimeError): self.run_real(m, phase=phase)
        with self.assertRaises(RuntimeError): self.run_real(m, phase='start-only-four', restart='always')

    def test_real_replay_cannot_certify_stale_A_when_B_started_or_security_changed(self):
        m = manifest(); rows = self.rows(m)
        b = {'_PID': str(m['controllers'][0]['pid']), '_UID': '0', '_SYSTEMD_UNIT': 'hound-ci-1.service',
             '_BOOT_ID': BOOT_HEX, '_SYSTEMD_INVOCATION_ID': invocation(1), '__CURSOR': 'later-B', '__MONOTONIC_TIMESTAMP': '40000001',
             '__REALTIME_TIMESTAMP': str(finish.micros(m['drained_utc']) + 1),
             'MESSAGE': 'HOUND_CI slot=1 name=hound-ci-1-abcdef012345 repo=xmit-dev/ultimator disposable VM started'}
        with self.assertRaisesRegex(RuntimeError, 'Latest root VM'): self.run_real(m, rows + [b])
        security = next(row for row in rows if row.get('MESSAGE', '').startswith('HOUND_CI QEMU_SECURITY_VERIFIED pid=401'))
        security['MESSAGE'] = security['MESSAGE'].replace('pid=401', 'pid=999')
        with self.assertRaisesRegex(RuntimeError, 'Latest root VM'): self.run_real(m, rows)

    def test_real_replay_ignores_NEW_invocation_records_including_reused_old_PID(self):
        m = manifest(); rows = self.rows(m)
        later = str(int(m['drain_witness']['1']['manager_monotonic']) + 10)
        realtime = str(finish.micros(m['drained_utc']) + 10)
        new = 'e' * 32
        rows += [
            # The NEW controller's own short invocation ending: never old proof.
            {'_PID': '1', '_UID': '0', '_COMM': 'systemd', 'UNIT': 'hound-ci-1.service', '_BOOT_ID': BOOT_HEX,
             'INVOCATION_ID': new, 'MESSAGE_ID': sorted(finish.MANAGER_IDS)[1], '__CURSOR': 'new-manager',
             '__MONOTONIC_TIMESTAMP': later, '__REALTIME_TIMESTAMP': realtime},
            # New invocation whose MainPID reused the OLD PID number.
            {'_PID': str(m['controllers'][0]['pid']), '_UID': '0', '_SYSTEMD_UNIT': 'hound-ci-1.service', '_BOOT_ID': BOOT_HEX,
             '_SYSTEMD_INVOCATION_ID': new, '__CURSOR': 'new-start', '__MONOTONIC_TIMESTAMP': later, '__REALTIME_TIMESTAMP': realtime,
             'MESSAGE': 'HOUND_CI slot=1 name=hound-ci-1-feedfacecafe repo=xmit-dev/ultimator disposable VM started'}]
        self.assertEqual(len(self.run_real(m, rows)), 4)
        # The ORIGINAL invocation's later START still revokes the old proof.
        rows[-1] = {**rows[-1], '_SYSTEMD_INVOCATION_ID': invocation(1)}
        with self.assertRaisesRegex(RuntimeError, 'Latest root VM'): self.run_real(m, rows)
        # And a record from another boot, or without attributable invocation, HOLDS.
        for change in ({'_BOOT_ID': 'b' * 32}, {'_SYSTEMD_INVOCATION_ID': None}):
            current = copy.deepcopy(rows[:-1]) + [{key: value for key, value in {**rows[-1], **change}.items() if value is not None}]
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'pinned boot|attributable'):
                self.run_real(m, current)

    def test_guest_forged_START_SECURITY_and_completed_markers_never_supply_root_identity(self):
        m = manifest(); rows = self.rows(m)
        original = copy.deepcopy(rows)
        for row in original:
            if row.get('_PID') == '101' and 'MESSAGE' in row:
                forged = copy.deepcopy(row); forged['_UID'] = '1000'; forged['__CURSOR'] += '-guest'
                forged['MESSAGE'] = forged['MESSAGE'].replace('000000000001', 'abcdef012345')
                rows.append(forged)
        rows.sort(key=lambda row: int(row['__MONOTONIC_TIMESTAMP']))
        self.assertEqual(len(self.run_real(m, rows)), 4)
        rows = [row for row in rows if not (row.get('_PID') == '101' and row.get('_UID') == '0' and
                                          'disposable VM started' in row.get('MESSAGE', ''))]
        with self.assertRaisesRegex(RuntimeError, 'lacks its root START'): self.run_real(m, rows)

    def test_actual_activation_original_cgroup_contract_now_matches_waiter_armer(self):
        m = manifest()
        armed = copy.deepcopy(m); armed['phase'] = 'armed-awaiting-job-completion'
        self.assertEqual(real_waiter.validate_manifest(armed), m['controllers'])
        # Kernel removal is mocked; the REAL activation contract validates the
        # same full /hound.slice/hound-ci.slice original cgroup identity.
        with patch.object(Path, 'lstat', side_effect=FileNotFoundError):
            real_activation.empty_original_cgroup(m['controllers'][0], m['drain_witness']['1'], {'ControlGroup': ''})

    @contextmanager
    def full_activation_fixture(self, later_B=False, historical=False, post_gate=False, no_gate_registration=False):
        # Reuse ONLY the temporary filesystem / manager fixture. Its synthetic
        # validator is REPLACED by the actual finisher module, not mocked.
        fixture_spec = importlib.util.spec_from_file_location('activation_fs_fixture', Path(__file__).with_name('test_activate.py'))
        fixture_module = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture_module)
        fixture_module.act = real_activation
        with tempfile.TemporaryDirectory() as folder:
            fixture = fixture_module.Fixture(folder)
            try:
                fixture.validator = finish
                fixture.manifest = (post_gate_manifest() if post_gate else
                                    historical_manifest() if historical else manifest())
                if no_gate_registration:
                    fixture.manifest['gates']['1'].update(registration=None, registration_after_gate=None,
                                                         host_qemu_before_gate=None)
                    fixture.manifest['gates']['1']['first_registration_read']['present'] = False
                fixture.write_manifests()
                m = fixture.manifest
                rows = self.rows(m)
                if later_B:
                    rows.append({'_PID': '101', '_UID': '0', '_SYSTEMD_UNIT': 'hound-ci-1.service',
                                 '_BOOT_ID': BOOT_HEX, '_SYSTEMD_INVOCATION_ID': invocation(1), '__CURSOR': 'later-B', '__MONOTONIC_TIMESTAMP': '40000001',
                                 '__REALTIME_TIMESTAMP': str(finish.micros(m['drained_utc']) + 1),
                                 'MESSAGE': 'HOUND_CI slot=1 name=hound-ci-1-abcdef012345 repo=xmit-dev/ultimator disposable VM started'})
                data = b''.join(finish.canonical(row) + b'\n' for row in rows)
                def process(*args, **kwargs):
                    assert args[0][0] == 'journalctl'
                    return SimpleNamespace(stdout=io.BytesIO(data), wait=lambda: 0, poll=lambda: 0)
                receipts = [] if historical else cleanups(m)
                if post_gate:
                    receipts[0] = cleanup(m, name=m['drain_witness']['1']['vm_history'][0]['name'])
                    receipts[0]['utc'] = '2026-10-05T12:21:01Z'
                    receipts.append(cleanup(m, runner_id=901))
                receipts.sort(key=lambda row: f'cleanup-{row["slot"]}-{row["id"]}.json')
                cert = certificate(m); cert['cleanup_receipts'] = receipts
                for filename, value in [('actions-terminal.json', cert)] + [
                        (f'cleanup-{row["slot"]}-{row["id"]}.json', row) for row in receipts]:
                    target = real_activation.STATE / filename
                    target.write_bytes(finish.canonical(value) + b'\n'); target.chmod(0o600)
                capture_dir = real_activation.STATE / 'actions-capture'
                capture_dir.mkdir(mode=0o700)
                for filename, capture_bytes in [('intent.json', finish.canonical(cert['invocation']['intent']) + b'\n'),
                                       ('invocation.json', finish.canonical(cert['invocation']) + b'\n'),
                                       ('stdout.json', finish.canonical(cert['collection']) + b'\n')]:
                    target = capture_dir / filename
                    target.write_bytes(capture_bytes); target.chmod(0o600)
                # The operator-authored lease binds THIS certificate/manifest.
                fixture.window.update(drain_nonce=m['drain_nonce'],
                                      manifest_sha256=real_activation.sha256(real_activation.canonical(m)),
                                      terminal_certificate_sha256=real_activation.sha256(
                                          (real_activation.STATE / 'actions-terminal.json').read_bytes()))
                fixture.write_window()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(finish, 'STATE', real_activation.STATE))
                    stack.enter_context(patch.object(finish, 'CANDIDATE', str(real_activation.CANDIDATE)))
                    stack.enter_context(patch.object(finish, 'CANDIDATE_SHA', real_activation.CANDIDATE_SHA))
                    stack.enter_context(patch.object(real_waiter, 'STATE', real_activation.STATE))
                    stack.enter_context(patch.object(finish, 'root_directory'))  # Temporary ordinary-user fixtures only.
                    stack.enter_context(patch.object(finish, 'validate_operator_source'))
                    stack.enter_context(patch.object(finish, 'load_source', return_value=real_waiter))
                    stack.enter_context(patch.object(finish.os, 'pidfd_open', side_effect=ProcessLookupError))
                    stack.enter_context(patch.object(real_waiter.os, 'sysconf', return_value=100))
                    stack.enter_context(patch.object(real_waiter.subprocess, 'Popen', side_effect=process))
                    stack.enter_context(patch.object(real_waiter, 'CgroupWatch', side_effect=lambda _: SimpleNamespace(sample=lambda: None, close=lambda: None)))
                    # Kernel membership of the tracked NEW MainPID, from the manager fixture.
                    def membership(pid):
                        rows = [values for values in fixture.manager.values() if values['MainPID'] == str(pid)]
                        assert len(rows) == 1, pid
                        return f'0::{rows[0]["ControlGroup"]}\n'
                    stack.enter_context(patch.object(finish, 'process_cgroup', side_effect=membership))
                    # Track calls WITHOUT replacing the real validator/revalidator.
                    rechecks = stack.enter_context(patch.object(finish, 'revalidate_final', wraps=finish.revalidate_final))
                    yield fixture, rechecks
            finally:
                fixture.close()


    def test_real_validator_exports_transition_api_and_missing_or_wrong_api_HOLDS_before_mutation(self):
        self.assertEqual(finish.ACTIVATION_TRANSITION_API, real_activation.TRANSITION_API)
        self.assertEqual(finish.NEW_UNITS, real_activation.UNITS)
        self.assertEqual((finish.CANDIDATE, finish.CANDIDATE_SHA), (str(real_activation.CANDIDATE), real_activation.CANDIDATE_SHA))
        for value in (None, 'tracked-controller-identity-v0'):
            with self.full_activation_fixture() as (fixture, _), patch.object(finish, 'ACTIVATION_TRANSITION_API', value):
                with self.subTest(api=value), self.assertRaisesRegex(RuntimeError, 'Integration HOLD:.*NEW-PID transition API'):
                    fixture.activate()
                self.assertEqual(fixture.commands, [])
                self.assertFalse((real_activation.STATE / 'activation.json').exists())

    def test_actual_CAPTURE_artifact_and_certificate_revalidation_without_NEW_PID_activation(self):
        for historical in (False, True):
            with self.full_activation_fixture(historical=historical) as (fixture, _):
                cert = finish.validate_certificate(fixture.manifest)
                self.assertEqual(cert['collection'], finish.read_capture(fixture.manifest)[0])
                self.assertEqual(len(finish.revalidate_final(real_activation.DrainView(fixture.operator), fixture.manifest)), 4)
                self.assertEqual(fixture.commands, [])

    def test_actual_complete_activation_uses_real_certificate_replay_and_cleanup(self):
        with self.full_activation_fixture() as (fixture, rechecks):
            original_manifest = (real_activation.STATE / 'manifest.json').read_bytes()
            fixture.activate()
            self.assert_tracked_activation(fixture, rechecks)
            self.assertEqual((real_activation.STATE / 'manifest.json').read_bytes(), original_manifest)
            self.assertEqual(fixture.manifest['phase'], finish.HARDWARE_PHASE)

    # 1 initial + 2 per change (pre + post-intent) x 15 changes + 1 final
    # (all four hold unlinks + one daemon-reload form ONE change).
    RECHECKS = 32

    def assert_tracked_activation(self, fixture, rechecks):
        self.assertEqual(len(rechecks.call_args_list), self.RECHECKS)
        receipt = fixture.receipt()
        self.assertEqual(receipt['phase'], 'new-four-started-awaiting-runtime-proof')
        self.assertEqual(fixture.commands, [['systemctl', 'daemon-reload'], ['systemctl', 'daemon-reload']] +
                         [['systemctl', '--job-mode=fail', 'start', '--', f'hound-ci-{slot}.service'] for slot in range(1, 5)])
        for slot in range(1, 5):
            record = receipt['slot_starts'][str(slot)]
            self.assertEqual(record['stage'], 'started')
            self.assertEqual(record['pre_start']['invocation_id'], invocation(slot))
            self.assertEqual(record['pre_start']['main_pid'], '0')
            self.assertEqual(record['result']['invocation_id'], f'{slot + 10:x}' * 32)
            self.assertEqual(record['result']['job'], {'type': 'start', 'mode': 'fail', 'result': 'done'})
            self.assertEqual(receipt['new_controllers'][str(slot)]['pid'], record['result']['pid'])
        # Each later start recheck validated EXACTLY the earlier tracked slots.
        phases = [call.args[0].activation_phase for call in rechecks.call_args_list]
        self.assertEqual(phases[-1], 'four-new-started-awaiting-runtime-proof')

    def test_actual_certificate_and_capture_are_revalidated_at_every_later_start_phase(self):
        tamperings = {
            'capture-stdout': lambda: (real_activation.STATE / 'actions-capture' / 'stdout.json').write_bytes(b'{}\n'),
            'capture-missing': lambda: (real_activation.STATE / 'actions-capture' / 'invocation.json').unlink(),
            'cleanup-receipt': lambda: (real_activation.STATE / 'cleanup-1-201.json').write_bytes(
                finish.canonical({**finish.decode((real_activation.STATE / 'cleanup-1-201.json').read_bytes()), 'success': False})),
        }
        for label, tamper in tamperings.items():
            with self.full_activation_fixture() as (fixture, _):
                original = real_activation.Journal.save
                done = []
                def save(journal):
                    original(journal)
                    if not done and journal.value.get('slot_starts', {}).get('1', {}).get('stage') == 'started':
                        done.append(True); tamper()
                with patch.object(real_activation.Journal, 'save', save), self.subTest(tamper=label):
                    with self.assertRaises(RuntimeError): fixture.activate()
                starts = fixture.receipt()['slot_starts']
                self.assertEqual(set(starts), {'1'})  # slot 2 intent never written
                self.assertEqual(sum(argv[:3] == ['systemctl', '--job-mode=fail', 'start'] for argv in fixture.commands), 1)
                self.assertFalse(any('stop' in argv or 'kill' in argv for argv in fixture.commands))

    def test_actual_complete_activation_holds_for_fresh_B_or_changed_cleanup_before_mutation(self):
        with self.full_activation_fixture(later_B=True) as (fixture, _):
            with self.assertRaisesRegex(RuntimeError, 'Latest root VM'): fixture.activate()
            self.assertEqual(fixture.commands, [])
            self.assertFalse((real_activation.GCROOTS / real_activation.ROOT_NAME).exists())
        with self.full_activation_fixture() as (fixture, _):
            receipt = cleanup(fixture.manifest); receipt['success'] = False
            (real_activation.STATE / 'cleanup-1-201.json').write_bytes(finish.canonical(receipt))
            with self.assertRaisesRegex(RuntimeError, 'did not positively finish'): fixture.activate()
            self.assertEqual(fixture.commands, [])

    def test_actual_activation_missing_one_or_all_gate_positive_DELETE_files_HOLDS(self):
        for missing in ((1,), (1, 2, 3, 4)):
            for matching_certificate in (False, True):
                with self.full_activation_fixture() as (fixture, _):
                    for slot in missing:
                        (real_activation.STATE / f'cleanup-{slot}-{200 + slot}.json').unlink()
                    if matching_certificate:
                        # A malformed empty/partial certificate must fail too,
                        # not just inequality with its original full sidecar.
                        path = real_activation.STATE / 'actions-terminal.json'
                        cert = finish.decode(path.read_bytes())
                        cert['cleanup_receipts'] = [row for row in cert['cleanup_receipts'] if row['slot'] not in missing]
                        path.write_bytes(finish.canonical(cert))
                    with self.subTest(missing=missing, matching_certificate=matching_certificate), \
                         self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'):
                        fixture.activate()
                    self.assertEqual(fixture.commands, [])
                    self.assertFalse((real_activation.GCROOTS / real_activation.ROOT_NAME).exists())
                    self.assertFalse((real_activation.STATE / 'activation.json').exists())

    def test_actual_activation_adopted_post_gate_VM_without_registration_requires_own_DELETE(self):
        with self.full_activation_fixture(post_gate=True) as (fixture, rechecks):
            fixture.activate()
            self.assert_tracked_activation(fixture, rechecks)
        with self.full_activation_fixture(post_gate=True) as (fixture, _):
            (real_activation.STATE / 'cleanup-1-901.json').unlink()
            path = real_activation.STATE / 'actions-terminal.json'
            cert = finish.decode(path.read_bytes())
            cert['cleanup_receipts'] = [row for row in cert['cleanup_receipts'] if row['id'] != 901]
            path.write_bytes(finish.canonical(cert))
            with self.assertRaisesRegex(RuntimeError, 'coverage UNKNOWN/HOLD'): fixture.activate()
            self.assertEqual(fixture.commands, [])
            self.assertFalse((real_activation.GCROOTS / real_activation.ROOT_NAME).exists())

    def test_actual_activation_lost_original_inflight_DELETE_is_unknown_not_absence_proof(self):
        with self.full_activation_fixture(no_gate_registration=True) as (fixture, _):
            (real_activation.STATE / 'cleanup-1-201.json').unlink()
            path = real_activation.STATE / 'actions-terminal.json'
            cert = finish.decode(path.read_bytes())
            cert['cleanup_receipts'] = [row for row in cert['cleanup_receipts'] if row['slot'] != 1]
            path.write_bytes(finish.canonical(cert))
            with self.assertRaisesRegex(RuntimeError, 'requires separately authorized reconciliation'):
                fixture.activate()
            self.assertEqual(fixture.commands, [])

    def test_actual_activation_historical_pre_gate_erased_needs_no_gate_DELETE(self):
        with self.full_activation_fixture(historical=True) as (fixture, rechecks):
            self.assertEqual(finish.read_cleanup_receipts(fixture.manifest), [])
            fixture.activate()
            self.assert_tracked_activation(fixture, rechecks)

    def test_actual_activation_2020_or_future_completed_at_never_reaches_manager(self):
        for timestamp in ('2020-01-01T00:00:00Z', '2026-10-05T12:31:05.000001Z'):
            with self.full_activation_fixture() as (fixture, _):
                path = real_activation.STATE / 'actions-terminal.json'
                cert = finish.decode(path.read_bytes())
                cert['collection']['jobs'][0]['job']['completed_at'] = timestamp
                cert['collection_sha256'] = finish.digest(finish.canonical(cert['collection']))
                cert['invocation'] = invocation_fixture(fixture.manifest, cert['collection'])
                capture_dir = real_activation.STATE / 'actions-capture'
                (capture_dir / 'intent.json').write_bytes(finish.canonical(cert['invocation']['intent']))
                (capture_dir / 'invocation.json').write_bytes(finish.canonical(cert['invocation']))
                (capture_dir / 'stdout.json').write_bytes(finish.canonical(cert['collection']) + b'\n')
                path.write_bytes(finish.canonical(cert))
                with self.subTest(timestamp=timestamp), self.assertRaisesRegex(RuntimeError, 'authenticated VM START'):
                    fixture.activate()
                self.assertEqual(fixture.commands, [])




class RunEnumerationTests(unittest.TestCase):
    def test_deterministic_closed_windows_cover_rerun_horizon_to_drain(self):
        m = manifest(); plan = finish.enumeration_plan(m, finish.accepted_vms(m))
        earliest = finish.micros('2026-10-05T12:10:00+00:00') // 1000000
        self.assertEqual(plan['windows'][0][0], (earliest - 31 * 86400) // 21600 * 21600)
        self.assertEqual(plan['until'], finish.micros('2026-10-05T12:30:05+00:00') // 1000000)
        self.assertEqual(plan['windows'][-1][1], plan['until'])
        for (a, b), (c, _) in zip(plan['windows'], plan['windows'][1:]):
            self.assertEqual((b + 1, b - a + 1), (c, 21600))  # Disjoint, gapless, 6 h.
        self.assertLessEqual(len(plan['windows']), finish.MAX_WINDOWS)
        self.assertEqual(plan['scan_threshold_us'], earliest * 1000000 - 5000000)
        api = FakeAPI(); report = finish.collect(m, api)
        self.assertEqual(api.windows, [finish.window_route(*w) for w in plan['windows']])
        for route in api.windows:
            self.assertRegex(route + '&per_page=100&page=1', finish.GITHUB_SECOND)
        finish.validate_collection(report, m)

    def test_window_route_is_only_filtered_GET_scope(self):
        api = object.__new__(finish.GitHub); api.calls = 0; api.pages = []
        with patch.object(finish, 'ordinary_get', return_value=b'{}') as get:
            api.get(finish.window_route(1791201600, 1791223199) + '&per_page=100&page=2')
            self.assertIn('created=2026-10-05T12:00:00Z..2026-10-05T17:59:59Z', get.call_args.args[0][-1])
            for route in (f'repos/{finish.REPO}/actions/runs?per_page=100&page=1',
                          f'repos/{finish.REPO}/actions/runs?created=>=2026-10-05&per_page=100&page=1',
                          f'repos/{finish.REPO}/actions/runs?created=2026-10-05T12:00:00Z..2026-10-05T17:59:59Z&status=queued&per_page=100&page=1'):
                with self.subTest(route=route), self.assertRaisesRegex(RuntimeError, 'escaped'):
                    api.get(route)

    def test_run_outside_window_duplicate_or_unknown_status_holds(self):
        m = manifest()
        api = FakeAPI(); api.window = lambda route: [run_row(created='2020-01-01T00:00:00Z')]
        with self.assertRaisesRegex(RuntimeError, 'outside its requested closed window'): finish.collect(m, api)
        api = FakeAPI(); original = api.window
        api.window = lambda route: original(route) or [run_row(created=route.split('created=')[1].split('..')[0])]
        with self.assertRaisesRegex(RuntimeError, 'two disjoint windows'): finish.collect(m, api)
        for mutate in (lambda r: r.update(status=None), lambda r: r.update(updated_at='2026-10-05T11:00:00Z'),
                       lambda r: r.update(run_attempt=finish.MAX_ATTEMPTS + 1), lambda r: r['repository'].update(full_name='o/r')):
            api = FakeAPI(); mutate(api.runs[0])
            with self.subTest(run=api.runs[0]), self.assertRaises(RuntimeError): finish.collect(m, api)

    def test_old_completed_runs_listed_not_scanned_open_runs_always_scanned(self):
        m = manifest(); api = FakeAPI()
        api.runs.append(run_row(run_id=400, created='2026-10-01T00:00:00Z', updated='2026-10-05T12:09:54Z'))
        api.runs.append(run_row(run_id=401, created='2026-09-20T00:00:00Z', updated='2026-09-20T01:00:00Z', status='in_progress'))
        api.runs.append(run_row(run_id=402, created='2026-10-01T00:00:00Z', updated='2026-10-05T12:09:55Z'))
        for run_id in (401, 402):
            api.attempts[run_id] = None
        def get(route, original=api.get):
            if '/attempts/' in route and int(route.split('/')[-3]) in (401, 402):
                api.calls += 1
                return run_row(run_id=int(route.split('/')[-3]))
            return original(route)
        def paged(route, key, original=api.paged):
            if int(route.split('/')[-4]) in (401, 402):
                api.calls += 1
                api.pages.append({'route': route, 'key': key, 'pages': 1, 'total_count': 0})
                return []
            return original(route, key)
        api.get, api.paged = get, paged
        report = finish.collect(m, api)
        scanned = {run['run_id']: run['scanned'] for run in report['runs']}
        self.assertEqual(scanned, {400: False, 401: True, 402: True, 500: True})
        self.assertNotIn(f'repos/{finish.REPO}/actions/runs/400/attempts/1/jobs', {p['route'] for p in report['paging']})
        finish.validate_collection(report, m)
        for mutate in (lambda r: next(x for x in r['runs'] if x['run_id'] == 400).update(scanned=True),
                       lambda r: next(x for x in r['runs'] if x['run_id'] == 402).update(scanned=False),
                       lambda r: next(x for x in r['runs'] if x['run_id'] == 401).update(status='completed'),
                       lambda r: r['runs'].reverse(),
                       lambda r: next(p for p in r['paging'] if p['key'] == 'workflow_runs').update(passes=[1]),
                       lambda r: next(p for p in r['paging'] if p['key'] == 'workflow_runs' and p['total_count']).update(total_count=0, pages=1)):
            current = copy.deepcopy(report); mutate(current)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                finish.validate_collection(current, m)

    def test_collection_before_windows_close_holds(self):
        class Early(FrozenDateTime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromisoformat('2026-10-05T12:30:04+00:00')
        with patch.object(finish, 'datetime', Early), self.assertRaisesRegex(RuntimeError, 'not yet closed'):
            finish.collect(manifest(), FakeAPI())
        report = collection(manifest()); report['collected_utc'] = '2026-10-05T12:30:05+00:00'
        with self.assertRaisesRegex(RuntimeError, 'closed run windows'):
            finish.validate_collection(report, manifest())

    def api(self, rows):
        api = object.__new__(finish.GitHub)
        api.pages = []; api.calls = 0
        api.get = Mock(side_effect=rows)
        return api

    def test_capped_or_unconverged_windows_split_depth_first_and_validate(self):
        m = manifest(); plan = finish.enumeration_plan(m, finish.accepted_vms(m))
        api = FakeAPI(); api.split_over = 1
        for run_id, hour in ((301, '01'), (302, '03'), (303, '05')):
            api.runs.append(run_row(run_id=run_id, created=f'2026-10-01T{hour}:00:00Z', updated=f'2026-10-01T{hour}:30:00Z'))
        day = finish.micros('2026-10-01T00:00:00+00:00') // 1000000
        sept = finish.micros('2026-09-20T00:00:00+00:00') // 1000000
        api.unconverged.add(finish.window_route(sept, sept + 21599))
        report = finish.collect(m, api)
        splits = {p['route']: p['calls'] for p in report['paging'] if p['key'] == 'workflow_runs_split'}
        self.assertEqual(splits, {finish.window_route(day, day + 21599): 1,
                                  finish.window_route(day + 10800, day + 21599): 1,
                                  finish.window_route(sept, sept + 21599): 3})
        index = api.windows.index(finish.window_route(day, day + 21599))
        self.assertEqual(api.windows[index:index + 5], [finish.window_route(*w) for w in (
            (day, day + 21599), (day, day + 10799), (day + 10800, day + 21599),
            (day + 10800, day + 16199), (day + 16200, day + 21599))])  # depth-first
        self.assertEqual({r['run_id'] for r in report['runs']}, {301, 302, 303, 500})
        _, leaves = finish.validate_paging(report['paging'], plan)
        self.assertEqual(len(leaves), len(plan['windows']) + 3)
        finish.validate_collection(report, m)
        def drop(route):
            return lambda r: r['paging'].remove(next(p for p in r['paging'] if p['route'] == route))
        child = finish.window_route(day + 10800, day + 16199)
        for mutate, message in (
                (drop(finish.window_route(day + 10800, day + 21599)), 'outside the deterministic partition|neither listed'),
                (drop(child), 'neither listed nor split'),
                (lambda r: next(p for p in r['paging'] if p['route'] in splits).update(calls=0), 'split call count'),
                (lambda r: next(p for p in r['paging'] if p['route'] in splits).update(calls=2), 'request accounting'),
                (lambda r: r['paging'].append({'route': finish.window_route(day, day + 59), 'key': 'workflow_runs',
                                               'total_count': 0, 'pages': 1, 'passes': [1, 1]}), 'outside the deterministic partition'),
                (lambda r: next(p for p in r['paging'] if p['route'] == child).update(total_count=0), 'differs from its converged'),
                (lambda r: r['paging'][r['paging'].index(next(p for p in r['paging'] if p['route'] == finish.window_route(day, day + 21599)))].update(
                    key='workflow_runs', total_count=3, pages=1, passes=[1, 1]) or r['paging'][-1].pop('calls', None), 'Run window paging|split')):
            current = copy.deepcopy(report); mutate(current)
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                finish.validate_collection(current, m)

    def test_split_window_halves_exactly_down_to_minimum_then_holds(self):
        self.assertEqual(finish.split_window((0, 119)), ((0, 59), (60, 119)))
        self.assertEqual(finish.split_window((0, 21599)), ((0, 10799), (10800, 21599)))
        self.assertEqual(finish.split_window((10, 130)), ((10, 69), (70, 130)))
        with self.assertRaisesRegex(RuntimeError, 'cannot be split further'):
            finish.split_window((0, 118))
        class Always:
            pages, calls = [], 0
            def window(self, route):
                split = finish.WindowSplit('cap'); split.calls = 1; raise split
        with self.assertRaisesRegex(RuntimeError, 'cannot be split further'):
            finish.list_windows(Always(), {'windows': [(0, 21599)]})
        api = FakeAPI(); api.split_over = 0
        api.runs = [run_row(run_id=i, created=f'2026-10-01T0{i % 6}:00:00Z') for i in range(1, 4)]
        with patch.object(finish, 'MAX_LEAF_WINDOWS', 2), self.assertRaisesRegex(RuntimeError, 'leaf bound'):
            finish.list_windows(api, {'windows': [(finish.micros('2026-10-01T00:00:00+00:00') // 1000000,) * 1 +
                                                  (finish.micros('2026-10-01T05:59:59+00:00') // 1000000,)]})

    def test_GitHub_window_raises_split_with_spent_calls_but_anomalies_HOLD(self):
        route = finish.window_route(1791201600, 1791223199)
        def page(*ids, total=None):
            return {'total_count': len(ids) if total is None else total, 'workflow_runs': [{'id': i} for i in ids]}
        for rows, calls in (([page(1, total=1000)], 1), ([page(1), page(1, 2), page(1, 2, 3)], 3),
                            ([page(1, 2), {'total_count': 1000, 'workflow_runs': []}], 2)):
            api = self.api(rows); answers = api.get.side_effect
            def counted(route, answers=answers, api=api):
                api.calls += 1; return next(answers)
            api.get = counted
            with self.subTest(rows=rows), self.assertRaises(finish.WindowSplit) as raised:
                api.window(route)
            self.assertEqual((raised.exception.calls, api.calls, api.pages), (calls, calls, []))
        for rows in ([page(1, 2), page(1)], [page(1, 2), page(1, 2, total=1)]):
            api = self.api(rows)
            with self.subTest(rows=rows), self.assertRaises(RuntimeError) as raised:
                api.window(route)
            self.assertNotIsInstance(raised.exception, finish.WindowSplit)

    def test_window_converges_with_monotonic_insertion_and_holds_otherwise(self):
        route = finish.window_route(1791201600, 1791223199)
        def page(*ids, total=None):
            return {'total_count': len(ids) if total is None else total, 'workflow_runs': [{'id': i} for i in ids]}
        api = self.api([page(1, 2), page(3, 1, 2), page(3, 1, 2)])
        self.assertEqual(sorted(row['id'] for row in api.window(route)), [1, 2, 3])
        self.assertEqual(api.pages, [{'route': route, 'key': 'workflow_runs', 'total_count': 3, 'pages': 1, 'passes': [1, 1, 1]}])
        ids = lambda top, bottom: [{'id': i} for i in range(top, bottom - 1, -1)]
        # A newly visible run (251) shifts page 2 by one mid-pass: the shifted
        # duplicate (151) is skipped, the pass is incomplete (150 < 151), and
        # two later identical complete passes converge.
        stable = [{'total_count': 151, 'workflow_runs': ids(251, 152)}, {'total_count': 151, 'workflow_runs': ids(151, 101)}]
        api = self.api([{'total_count': 150, 'workflow_runs': ids(250, 151)},
                        {'total_count': 151, 'workflow_runs': ids(151, 101)}] + stable + stable)
        self.assertEqual(sorted(row['id'] for row in api.window(route)), list(range(101, 252)))
        self.assertEqual(api.pages[-1]['passes'], [2, 2, 2])
        for rows, message in (([page(1, 2), page(1)], 'disappeared|shrank'),
                              ([page(1, 2), page(1, 2, total=1)], 'shrank|disappeared|converge'),
                              ([page(1), page(1, 2), page(1, 2, 3)], 'converge'),
                              ([page(1, total=1001)], 'filtered-listing cap'),
                              ([page(1, 2, total=2), page(1, total=3)], 'converge|disappeared')):
            api = self.api(rows)
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, message):
                api.window(route)


class PinnedGhConfigTests(unittest.TestCase):
    def tree(self, folder, hosts, mode=0o600):
        path = Path(folder) / 'home' / 'pcarrier' / '.config' / 'gh'
        path.mkdir(parents=True)
        (path / 'hosts.yml').write_text(hosts); (path / 'hosts.yml').chmod(mode)
        return tuple(Path(folder).parts[1:]) + ('home', 'pcarrier', '.config', 'gh', 'hosts.yml')

    def owned(self):
        real = os.fstat
        return patch.object(finish.os, 'fstat', side_effect=lambda fd: SimpleNamespace(
            **{key: getattr(real(fd), key) for key in ('st_mode', 'st_size', 'st_nlink')}, st_uid=1000))

    TOKEN = 'gho_' + 'A' * 36

    def test_only_github_login_and_token_are_extracted_never_overrides(self):
        hosts = (f'github.com:\n    users:\n        pcarrier:\n            oauth_token: {self.TOKEN}\n'
                 f'    http_unix_socket: /tmp/forged.sock\n    oauth_token: {self.TOKEN}\n    user: pcarrier\n'
                 f'example.com:\n    oauth_token: ghp_{"B" * 36}\n')
        with tempfile.TemporaryDirectory() as folder:
            parts = self.tree(folder, hosts)
            with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned():
                self.assertEqual(finish.operator_token(), ('pcarrier', self.TOKEN))
        for bad in ('github.com:\n    user: pcarrier\n',  # keyring-only
                    f'github.com:\n    oauth_token: {self.TOKEN}\n    oauth_token: {self.TOKEN}\n    user: p\n',
                    f'"github.com":\n    oauth_token: {self.TOKEN}\n    user: p\n',
                    f'github.com:\n    oauth_token: "{self.TOKEN}"\n    user: p\n'):
            with tempfile.TemporaryDirectory() as folder:
                parts = self.tree(folder, bad)
                with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned(), self.subTest(bad=bad), \
                     self.assertRaises(RuntimeError):
                    finish.operator_token()

    def test_token_walk_is_nofollow_owned_single_link(self):
        hosts = f'github.com:\n    oauth_token: {self.TOKEN}\n    user: pcarrier\n'
        with tempfile.TemporaryDirectory() as folder:
            parts = self.tree(folder, hosts)
            config = Path('/', *parts[:-2])
            (config / 'gh').rename(config / 'real-gh'); (config / 'gh').symlink_to('real-gh')
            with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned(), self.assertRaises(OSError):
                finish.operator_token()
        with tempfile.TemporaryDirectory() as folder:
            parts = self.tree(folder, hosts)
            os.link(Path('/', *parts), Path(folder) / 'second-link')
            with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned(), self.assertRaisesRegex(RuntimeError, 'identity'):
                finish.operator_token()
        with tempfile.TemporaryDirectory() as folder:
            parts = self.tree(folder, hosts)
            real = os.fstat
            root_owned = patch.object(finish.os, 'fstat', side_effect=lambda fd: SimpleNamespace(
                **{key: getattr(real(fd), key) for key in ('st_mode', 'st_size', 'st_nlink')}, st_uid=0))
            with patch.object(finish, 'USER_GH_HOSTS', parts), root_owned, self.assertRaisesRegex(RuntimeError, 'identity'):
                finish.operator_token()  # A root file is never copied out to the collector.

    def test_install_check_remove_root_owned_collector_gid_only(self):
        hosts = f'github.com:\n    oauth_token: {self.TOKEN}\n    user: pcarrier\n'
        with tempfile.TemporaryDirectory() as folder:
            parts = self.tree(folder, hosts)
            target = Path(folder) / 'run' / 'hound-ci-actions-gh'; target.parent.mkdir()
            real_lstat = Path.lstat
            fake = lambda path: SimpleNamespace(**{key: getattr(real_lstat(path), key) for key in ('st_mode', 'st_nlink')},
                                                st_uid=0, st_gid=finish.COLLECTOR_GID)
            with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned(), patch.object(finish, 'GH_CONFIG_DIR', target), \
                 patch.object(finish, 'collector_gid_unshared'), patch.object(finish, 'root_directory'), \
                 patch.object(finish.os, 'chown') as chown, patch.object(finish.os, 'fchown') as fchown, \
                 patch.object(Path, 'lstat', autospec=True, side_effect=fake):
                finish.install_gh_config()
                chown.assert_called_once_with(target, 0, finish.COLLECTOR_GID)
                self.assertEqual([c.args[1:] for c in fchown.call_args_list], [(0, finish.COLLECTOR_GID)] * 2)
                self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o750)
                self.assertEqual({stat.S_IMODE(os.stat(target / n).st_mode) for n in ('config.yml', 'hosts.yml')}, {0o440})
                self.assertEqual((target / 'config.yml').read_bytes(), finish.GH_CONFIG)
                self.assertNotIn(b'http_unix_socket', (target / 'hosts.yml').read_bytes())
                self.assertIn(self.TOKEN.encode(), (target / 'hosts.yml').read_bytes())
                with patch.dict(os.environ, {'GH_CONFIG_DIR': str(target), 'HOME': str(target)}):
                    finish.check_gh_config()
                with patch.dict(os.environ, {'GH_CONFIG_DIR': str(target), 'HOME': '/home/pcarrier'}), \
                     self.assertRaisesRegex(RuntimeError, 'does not pin'):
                    finish.check_gh_config()
                with self.assertRaisesRegex(RuntimeError, 'Stale pinned gh config .*Remediation.*rm -r --'):
                    finish.install_gh_config()  # Never reused, overwritten or removed here.
                self.assertTrue((target / 'hosts.yml').exists())
                os.chmod(target / 'config.yml', 0o640); (target / 'config.yml').write_bytes(b'http_unix_socket: /x\n')
                os.chmod(target / 'config.yml', 0o440)
                with self.assertRaisesRegex(RuntimeError, 'content drift'):
                    finish.check_gh_config(root=True)
                finish.remove_gh_config()
                self.assertFalse(target.exists())

    def producer_patches(self, stack, child):
        stack.enter_context(patch.object(finish, 'root_directory'))
        stack.enter_context(patch.object(finish.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_gid=100, pw_dir='/home/pcarrier')))
        stack.enter_context(patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=0o100555, st_uid=0)))
        stack.enter_context(patch.object(finish.os, 'access', return_value=True))
        stack.enter_context(patch.object(finish.os, 'pipe2', side_effect=[(10, 11), (12, 13)]))
        stack.enter_context(patch.object(finish.os, 'close'))
        stack.enter_context(patch.object(finish.os, 'read', return_value=b'COLLECTOR_READY\n'))
        stack.enter_context(patch.object(finish.os, 'write', return_value=3))
        stack.enter_context(patch.object(finish.select, 'select', return_value=([10], [], [])))
        stack.enter_context(patch.object(finish, 'observe_child', return_value={}))
        stack.enter_context(patch.object(finish.subprocess, 'Popen', return_value=child))

    def test_termination_signal_or_timeout_removes_copy_and_restores_handlers(self):
        before = {number: finish.signal.getsignal(number) for number in finish.CAPTURE_SIGNALS}
        for failure in ('signal', 'timeout'):
            order = []
            child = SimpleNamespace(pid=9999, returncode=None, stdin=io.BytesIO(), stdout=io.BytesIO(), kill=Mock(), wait=Mock(return_value=-9))
            def pump(*args):
                if failure == 'signal':
                    # The handler run_producer installed, as the kernel would invoke it.
                    finish.signal.getsignal(finish.signal.SIGTERM)(finish.signal.SIGTERM, None)
                raise RuntimeError('Collector capture timeout; UNKNOWN/HOLD')
            with ExitStack() as stack:
                target = stack.enter_context(fake_gh_config(order))
                self.producer_patches(stack, child)
                stack.enter_context(patch.object(finish, 'pump_child', side_effect=pump))
                with self.subTest(failure=failure), self.assertRaises((finish.CaptureSignal, RuntimeError)) as raised:
                    finish.run_producer(manifest(), b'{}\n')
                self.assertEqual(type(raised.exception), finish.CaptureSignal if failure == 'signal' else RuntimeError)
                self.assertEqual(order, ['install', ('check', True), 'remove']); self.assertFalse(target.exists())
                child.kill.assert_called_once_with()
            self.assertEqual({number: finish.signal.getsignal(number) for number in finish.CAPTURE_SIGNALS}, before)

    def test_real_signal_during_Popen_is_deferred_so_the_child_is_never_orphaned(self):
        order = []
        child = SimpleNamespace(pid=9999, returncode=None, stdin=io.BytesIO(), stdout=io.BytesIO(), kill=Mock(), wait=Mock(return_value=-9))
        def spawn(*args, **kwargs):
            # A REAL SIGTERM arrives while fork+exec is in flight.
            os.kill(os.getpid(), finish.signal.SIGTERM)
            order.append('spawned')
            return child
        with ExitStack() as stack:
            target = stack.enter_context(fake_gh_config(order))
            self.producer_patches(stack, child)
            stack.enter_context(patch.object(finish.subprocess, 'Popen', side_effect=spawn))
            pump = stack.enter_context(patch.object(finish, 'pump_child'))
            with self.assertRaises(finish.CaptureSignal):
                finish.run_producer(manifest(), b'{}\n')
        self.assertEqual(order, ['install', 'spawned', ('check', True), 'remove']); self.assertFalse(target.exists())
        child.kill.assert_called_once_with(); child.wait.assert_called_once_with()  # recorded, killed, reaped
        pump.assert_not_called()

    def test_real_signal_during_cleanup_waits_until_cleanup_finished(self):
        order = []
        child = SimpleNamespace(pid=9999, returncode=None, stdin=io.BytesIO(), stdout=io.BytesIO(),
                                kill=Mock(side_effect=lambda: (order.append('kill'), os.kill(os.getpid(), finish.signal.SIGTERM))),
                                wait=Mock(side_effect=lambda: order.append('reap')))
        previous = finish.signal.signal(finish.signal.SIGTERM, lambda number, frame: order.append('late-signal'))
        try:
            with ExitStack() as stack:
                target = stack.enter_context(fake_gh_config(order))
                self.producer_patches(stack, child)
                stack.enter_context(patch.object(finish, 'pump_child', side_effect=RuntimeError('Collector capture timeout; UNKNOWN/HOLD')))
                with self.assertRaisesRegex(RuntimeError, 'capture timeout'):
                    finish.run_producer(manifest(), b'{}\n')
        finally:
            finish.signal.signal(finish.signal.SIGTERM, previous)
        # Kill, reap, check and removal all complete; the signal reaches the
        # ORIGINAL handler only afterwards, never interrupting the cleanup.
        self.assertEqual(order, ['install', 'kill', 'reap', ('check', True), 'remove', 'late-signal'])
        self.assertFalse(target.exists())
        self.assertEqual(finish.signal.pthread_sigmask(finish.signal.SIG_BLOCK, []), set())

    def test_stale_or_foreign_directory_HOLDS_with_remediation_and_is_never_removed(self):
        order = []
        with ExitStack() as stack:
            target = stack.enter_context(fake_gh_config(order))
            target.mkdir()
            self.producer_patches(stack, SimpleNamespace(pid=9999))
            with self.assertRaises(RuntimeError) as raised:
                finish.run_producer(manifest(), b'{}\n')
            message = str(raised.exception)
            self.assertNotIn('\n', message)
            self.assertIn(f'rm -r -- {target}', message)
            self.assertEqual(order, []); self.assertTrue(target.exists())
        # A directory appearing between the check and mkdir is also never removed.
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'gh'; target.mkdir()
            ownership = {}
            with patch.object(finish, 'GH_CONFIG_DIR', target), patch.object(finish, 'collector_gid_unshared'), \
                 patch.object(finish, 'root_directory'), patch.object(finish, 'operator_token', return_value=('pcarrier', self.TOKEN)), \
                 patch.object(finish.os.path, 'lexists', return_value=False), patch.object(finish, 'remove_gh_config') as removed:
                with self.assertRaisesRegex(RuntimeError, 'Stale pinned gh config'):
                    finish.install_gh_config(ownership)
                removed.assert_not_called(); self.assertNotIn('created', ownership)

    def test_mkdir_under_umask077_with_signals_blocked_and_ownership_flag(self):
        events = []
        real_mkdir = os.mkdir
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'gh'
            def mkdir(path, mode):
                events.append(('mkdir', mode, os.umask(0o077),
                               set(finish.signal.pthread_sigmask(finish.signal.SIG_BLOCK, [])) >= set(finish.CAPTURE_SIGNALS)))
                real_mkdir(path, mode)
            ownership = {}
            with patch.object(finish, 'GH_CONFIG_DIR', target), patch.object(finish, 'collector_gid_unshared'), \
                 patch.object(finish, 'root_directory'), patch.object(finish, 'operator_token', return_value=('pcarrier', self.TOKEN)), \
                 patch.object(finish.os, 'mkdir', side_effect=mkdir), patch.object(finish.os, 'chown'), \
                 patch.object(finish.os, 'fchown'), patch.object(finish, 'check_gh_config'):
                finish.install_gh_config(ownership)
            self.assertEqual(events, [('mkdir', 0o700, 0o077, True)])
            self.assertEqual(ownership, {'created': True})
            self.assertFalse(set(finish.signal.pthread_sigmask(finish.signal.SIG_BLOCK, [])) & set(finish.CAPTURE_SIGNALS))
            self.assertEqual({stat.S_IMODE(os.stat(target / n).st_mode) for n in ('config.yml', 'hosts.yml')}, {0o440})

    def test_parse_errors_never_carry_file_contents(self):
        secret = 'gho_' + 'S' * 36
        for content in (f'github.com:\n    oauth_token: {secret}\n    user: \xff\n'.encode('latin-1'),
                        f'github.com:\n    oauth_token: {secret}\n'.encode()):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'home' / 'pcarrier' / '.config' / 'gh'; path.mkdir(parents=True)
                (path / 'hosts.yml').write_bytes(content)
                parts = tuple(Path(folder).parts[1:]) + ('home', 'pcarrier', '.config', 'gh', 'hosts.yml')
                with patch.object(finish, 'USER_GH_HOSTS', parts), self.owned(), self.assertRaises(RuntimeError) as raised:
                    finish.operator_token()
                import traceback
                rendered = ''.join(traceback.format_exception(raised.exception))
                self.assertNotIn(secret, rendered); self.assertNotIn('SSSS', rendered)
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__ or raised.exception.__context__ is None)

    def test_private_collector_gid_is_unshared_and_bound_everywhere(self):
        self.assertIn(f'os.getegid() == {finish.COLLECTOR_GID} and', finish.COLLECTOR_BOOTSTRAP)
        contract = finish.execution_contract(manifest())
        self.assertEqual((contract['gid'], contract['gh_config_dir'], contract['gh_config_sha256']),
                         (finish.COLLECTOR_GID, str(finish.GH_CONFIG_DIR), finish.digest(finish.GH_CONFIG)))
        absent = patch.object(finish.grp, 'getgrgid', side_effect=KeyError)
        accounts = [SimpleNamespace(pw_gid=100), SimpleNamespace(pw_gid=1000)]
        subgid = b'pcarrier:100000:65536\ndauriac:165536:65536\n'
        with absent, patch.object(finish.pwd, 'getpwall', return_value=accounts), \
             patch.object(finish.os.path, 'lexists', return_value=True), patch.object(finish, 'read_root_bytes', return_value=subgid) as read:
            finish.collector_gid_unshared()
        # The live /etc/subgid is root:root 0644: read with that EXACT mode.
        read.assert_called_once_with(Path('/etc/subgid'), 65536, 0o644)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'subgid'; path.write_bytes(subgid); path.chmod(0o644)
            real = os.fstat
            owned = lambda fd: os.stat_result(tuple(real(fd))[:4] + (0, 0) + tuple(real(fd))[6:])
            with patch.object(finish, 'root_directory'), patch.object(finish.os, 'fstat', side_effect=owned):
                self.assertEqual(finish.read_root_bytes(path, 65536, finish.SUBGID_MODE), subgid)
                with self.assertRaisesRegex(RuntimeError, 'mode invalid'):
                    finish.read_root_bytes(path, 65536)  # the store-file default refuses 0644
                path.chmod(0o664)
                with self.assertRaisesRegex(RuntimeError, 'mode invalid'):
                    finish.read_root_bytes(path, 65536, finish.SUBGID_MODE)
        for grp_patch, users, ranges in ((patch.object(finish.grp, 'getgrgid', return_value=object()), accounts, subgid),
                                         (absent, accounts + [SimpleNamespace(pw_gid=finish.COLLECTOR_GID)], subgid),
                                         (absent, accounts, b'x:2000000000:65536\n'), (absent, accounts, b'garbage\n')):
            with grp_patch, patch.object(finish.pwd, 'getpwall', return_value=users), \
                 patch.object(finish.os.path, 'lexists', return_value=True), patch.object(finish, 'read_root_bytes', return_value=ranges), \
                 self.subTest(ranges=ranges), self.assertRaises(RuntimeError):
                finish.collector_gid_unshared()


class RehearsalAndRetryTests(unittest.TestCase):
    UNTIL = finish.micros('2026-10-05T12:30:00+00:00') // 1000000  # FrozenDateTime is 12:31

    def request(self, **changes):
        value = {'schema': 1, 'kind': finish.REHEARSAL_KIND, 'until': self.UNTIL, 'earliest_us': (self.UNTIL - 3600) * 1000000,
                 'validator_source': '/nix/store/x-finish-drain.py', 'gh_sha256': 'a' * 64}
        value.update(changes)
        return value

    def test_rehearsal_lists_the_same_windows_splits_and_scans_without_direct_job_GETs(self):
        api = FakeAPI()
        result = finish.rehearse_collect(self.request(), api)
        plan = finish.closed_windows((self.UNTIL - 3600) * 1000000, self.UNTIL)
        self.assertEqual(api.windows, [finish.window_route(*w) for w in plan['windows']])
        self.assertEqual({key: result[key] for key in ('windows', 'leaves', 'splits', 'runs', 'scanned_runs', 'scanned_attempts')},
                         {'windows': len(plan['windows']), 'leaves': len(plan['windows']), 'splits': 0, 'runs': 1,
                          'scanned_runs': 1, 'scanned_attempts': 1})
        self.assertEqual(result['request_count'], 2 * len(plan['windows']) + 2)  # listings + attempt GET + job page
        self.assertFalse(any('/actions/jobs/' in p['route'] for p in api.pages))
        finish.validate_rehearsal_result(result)
        api = FakeAPI(); api.split_over = 1
        api.runs += [run_row(run_id=501, created='2026-10-04T01:00:00Z', updated='2026-10-04T01:30:00Z'),
                     run_row(run_id=502, created='2026-10-04T04:00:00Z', updated='2026-10-04T04:30:00Z')]
        result = finish.rehearse_collect(self.request(), api)
        self.assertEqual((result['splits'], result['leaves'], result['runs'], result['scanned_runs']),
                         (1, len(plan['windows']) + 1, 3, 1))
        old = FakeAPI(); old.runs[0].update(updated_at='2026-10-05T11:00:00Z', created_at='2026-10-05T10:00:00Z')
        self.assertEqual(finish.rehearse_collect(self.request(), old)['scanned_runs'], 0)

    def test_rehearsal_request_and_result_are_bounded(self):
        now = self.UNTIL + 60
        finish.validate_rehearsal_request(self.request(), now)
        for bad in (self.request(until=now), self.request(earliest_us=self.UNTIL * 1000000), self.request(kind='x'),
                    self.request(earliest_us=(self.UNTIL - 86401) * 1000000), self.request(gh_sha256='A' * 64),
                    dict(self.request(), extra=1)):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                finish.validate_rehearsal_request(bad, now)
        result = finish.rehearse_collect(self.request(), FakeAPI())
        for change in ({'request_count': finish.MAX_CALLS + 1}, {'elapsed_ms': finish.CAPTURE_TIMEOUT * 1000},
                       {'leaves': 0}, {'runs': -1}, {'kind': 'x'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                finish.validate_rehearsal_result({**result, **change})

    def test_root_rehearse_feeds_the_same_producer_and_requires_the_copy_removed(self):
        result = finish.rehearse_collect(self.request(), FakeAPI())
        output = finish.canonical(result) + b'\n'
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            pinned = Path(folder) / 'gh'
            stack.enter_context(patch.object(finish, 'GH_CONFIG_DIR', pinned))
            stack.enter_context(patch.object(finish, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, dont_write_bytecode=1),
                                                                       executable=str(finish.PYTHON))))
            stack.enter_context(patch.object(finish, 'python_executable', return_value=str(Path(str(finish.PYTHON)).resolve())))
            stack.enter_context(patch.object(finish.os, 'getuid', return_value=0))
            stack.enter_context(patch.object(finish.os, 'geteuid', return_value=0))
            real_stat = os.stat
            stack.enter_context(patch.object(finish.os, 'stat', side_effect=lambda p, *a, **k: SimpleNamespace(st_ino=1) if str(p).endswith('/ns/mnt') else real_stat(p, *a, **k)))
            stack.enter_context(patch.object(finish, 'read_root_bytes', return_value=b'reviewed bytes'))
            checked = stack.enter_context(patch.object(finish, 'validate_operator_source'))
            producer = stack.enter_context(patch.object(finish, 'run_producer', return_value=(output, {'child_exit': 0})))
            printed = stack.enter_context(patch('builtins.print'))
            self.assertEqual(finish.rehearse(), result)
            pseudo, data = producer.call_args.args
            self.assertEqual(pseudo, {'validator_source': str(Path(finish.__file__)), 'validator_sha256': finish.digest(b'reviewed bytes')})
            checked.assert_called_once_with(Path(finish.__file__), pseudo['validator_sha256'])
            request = json.loads(data)
            self.assertEqual((request['kind'], data, request['validator_source']), (finish.REHEARSAL_KIND, finish.canonical(request) + b'\n', pseudo['validator_source']))
            self.assertTrue(printed.call_args.args[0].startswith('HOUND_CI_ACTIONS_REHEARSED windows='))
            self.assertIn('gh_copy_removed=1', printed.call_args.args[0])
            producer.side_effect = lambda *a: (pinned.mkdir(), (output, {'child_exit': 0}))[1]
            with self.assertRaisesRegex(RuntimeError, 'survived the rehearsal'):
                finish.rehearse()
            producer.side_effect = None; producer.return_value = (finish.canonical({**result, 'request_count': finish.MAX_CALLS + 1}) + b'\n', {'child_exit': 0})
            pinned.rmdir()
            with self.assertRaisesRegex(RuntimeError, 'HOLD before arming'):
                finish.rehearse()

    def test_collect_stdin_dispatches_rehearsal_inside_the_unchanged_bootstrap(self):
        request = self.request(validator_source=str(Path(finish.__file__)))
        data = finish.canonical(request) + b'\n'
        argv = ['finish-drain.py', '--collect-stdin']
        with ExitStack() as stack:
            stack.enter_context(patch.object(finish.sys, 'argv', argv))
            stack.enter_context(patch.object(finish.os, 'geteuid', return_value=1000))
            stack.enter_context(patch.object(finish.os, 'getuid', return_value=1000))
            stack.enter_context(patch.object(finish.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(data))))
            stack.enter_context(patch.object(finish, 'sys', SimpleNamespace(argv=argv, stdin=SimpleNamespace(buffer=io.BytesIO(data)),
                                                                       flags=SimpleNamespace(isolated=1, dont_write_bytecode=1))))
            github = stack.enter_context(patch.object(finish, 'GitHub', return_value='api'))
            collect = stack.enter_context(patch.object(finish, 'rehearse_collect', return_value={'k': 1}))
            real = stack.enter_context(patch.object(finish, 'collect'))
            printed = stack.enter_context(patch('builtins.print'))
            finish.main()
        github.assert_called_once_with('a' * 64, pinned_config=True)
        collect.assert_called_once_with(request, 'api'); real.assert_not_called()
        self.assertEqual(printed.call_args.args[0], '{"k":1}')
        self.assertIn("sys.argv = [path, '--collect-stdin']", finish.COLLECTOR_BOOTSTRAP)

    def archive_fixture(self, stack, folder):
        state = Path(folder) / 'state'; state.mkdir(mode=0o700)
        stack.enter_context(patch.object(finish, 'STATE', state))
        stack.enter_context(patch.object(finish, 'GH_CONFIG_DIR', Path(folder) / 'gh'))
        stack.enter_context(patch.object(finish, 'read_public_json', return_value={'m': 1}))
        operator = stack.enter_context(patch.object(finish, 'root_operator'))
        def directory(path):
            if not path.is_dir() or path.is_symlink():
                raise RuntimeError('Root directory missing/unsafe')
        stack.enter_context(patch.object(finish, 'root_directory', side_effect=directory))
        stack.enter_context(patch('builtins.print'))
        return state, operator

    def test_archive_failed_capture_renames_only_an_uncertified_capture_bounded(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            state, operator = self.archive_fixture(stack, folder)
            with self.assertRaisesRegex(RuntimeError, 'Root directory missing'):
                finish.archive_failed_capture()
            for index in range(1, finish.FAILED_CAPTURES + 1):
                (state / 'actions-capture').mkdir(); (state / 'actions-capture' / 'intent.json').write_text('{}')
                self.assertEqual(finish.archive_failed_capture(), state / f'actions-capture-failed-{index}')
                self.assertFalse((state / 'actions-capture').exists())
                self.assertEqual((state / f'actions-capture-failed-{index}' / 'intent.json').read_text(), '{}')  # never edited
            operator.assert_called_with({'m': 1})
            (state / 'actions-capture').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'archive bound reached'):
                finish.archive_failed_capture()
            self.assertTrue((state / 'actions-capture').exists())
            (state / 'actions-capture-failed-3').rename(Path(folder) / 'moved')
            for blocker, message in ((state / 'actions-terminal.json', 'certificate exists'),
                                     (state / 'actions-terminal.tmp', 'certificate exists'),
                                     (Path(folder) / 'gh', 'rm -r --')):
                blocker.mkdir()
                with self.subTest(blocker=blocker.name), self.assertRaisesRegex(RuntimeError, message):
                    finish.archive_failed_capture()
                blocker.rmdir()
                self.assertTrue((state / 'actions-capture').exists())
            self.assertEqual(finish.archive_failed_capture(), state / 'actions-capture-failed-3')
            # certify/read_capture read ONLY actions-capture.
            self.assertEqual(finish.capture_paths()[0], state / 'actions-capture')


class BoundsTests(unittest.TestCase):
    def test_replay_and_manifest_bounds_are_shared_and_exceed_live_counts_with_margin(self):
        gate_spec = importlib.util.spec_from_file_location('bound_gate', Path(__file__).with_name('drain-gh-gate.py'))
        gate = importlib.util.module_from_spec(gate_spec); gate_spec.loader.exec_module(gate)
        self.assertEqual({finish.LIMIT, real_waiter.MANIFEST_LIMIT, real_activation.JSON_LIMIT, gate.MANIFEST_LIMIT},
                         {16 * 1024 * 1024})
        self.assertEqual((finish.MAX_SLOT_VMS, finish.MAX_VMS), (real_waiter.MAX_VM_HISTORY, real_waiter.MAX_ALL_VM_HISTORY))
        self.assertLess(finish.MAX_VMS, 4 * finish.MAX_SLOT_VMS)  # The all-slot cap can bind.
        self.assertGreaterEqual(finish.JOURNAL_LIMIT, real_activation.JOURNAL_LIMIT)
        live = {1: 75, 2: 59, 3: 74, 4: 62}  # Read-only counts, 2026-10-05 19:14 UTC.
        self.assertTrue(all(count * 20 < finish.MAX_SLOT_VMS for count in live.values()))
        self.assertGreater(finish.MAX_VMS, 15 * sum(live.values()))
        # A full-cap indented manifest stays well inside every reader's bound.
        vm = {'name': 'hound-ci-1-0123456789ab', 'qemu_pid': 4194304, 'security_verified': True,
              'start_monotonic': '9' * 16, 'start_realtime': '9' * 16, 'stop_monotonic': '9' * 16, 'stop_realtime': '9' * 16}
        size = len(json.dumps({'vm_history': [vm] * finish.MAX_VMS}, indent=2, sort_keys=True))
        self.assertLess(size * 2, finish.LIMIT)

    def test_full_cap_history_is_accepted_and_one_more_holds(self):
        m = manifest(); w = m['drain_witness']['1']; template = w['vm_history'][0]
        history = []
        for index in range(finish.MAX_SLOT_VMS):
            vm = copy.deepcopy(template)
            vm.update(name=f'hound-ci-1-a{index:011x}', start_monotonic=str(1000 + 2 * index),
                      stop_monotonic=str(1001 + 2 * index), qemu_pid=10000 + index,
                      start_realtime=str(finish.micros('2026-10-05T12:00:00+00:00') + 2 * index),
                      stop_realtime=str(finish.micros('2026-10-05T12:00:00+00:00') + 2 * index + 1))
            history.append(vm)
        last = history[-1]
        last.update(name=template['name'], start_monotonic='26000000', stop_monotonic='30000000',
                    start_realtime=template['start_realtime'], stop_realtime=template['stop_realtime'], qemu_pid=template['qemu_pid'])
        w['vm_history'] = history
        self.assertEqual(len([vm for vm in finish.accepted_vms(m) if vm['slot'] == 1]), 1)
        extra = copy.deepcopy(history[0]); extra.update(name='hound-ci-1-ffffffffffff', start_monotonic='10', stop_monotonic='11',
                                                        start_realtime=str(finish.micros('2026-10-05T11:59:00+00:00')),
                                                        stop_realtime=str(finish.micros('2026-10-05T11:59:01+00:00')), qemu_pid=9)
        w['vm_history'].insert(0, extra)
        with self.assertRaisesRegex(RuntimeError, 'history missing'):
            finish.accepted_vms(m)

    def test_capture_refuses_before_windows_close_without_creating_anything(self):
        m = manifest()
        plan = finish.enumeration_plan(m, finish.accepted_vms(m))
        with patch.object(finish, 'read_public_json', return_value=m), patch.object(finish, 'root_operator'), \
             patch.object(finish.time, 'time', return_value=plan['until'] + 60), patch.object(Path, 'mkdir') as made, \
             patch.object(finish, 'run_producer') as run:
            with self.assertRaisesRegex(RuntimeError, 'not yet closed'):
                finish.capture()
            made.assert_not_called(); run.assert_not_called()


class PublicReadTests(unittest.TestCase):
    def test_duplicate_keys_nonfinite_data_rejected(self):
        for data in (b'{"id":1,"id":2}', b'{"id":NaN}', b'{', b'\xff'):
            with self.assertRaises(RuntimeError): finish.decode(data)
        self.assertEqual(finish.decode(b'{"id":1}'), {'id': 1})

    def test_cleanup_only_exact_named_public_files_intents_and_listing_errors_hold(self):
        m = manifest(); records = cleanups(m)
        names = [f'cleanup-{row["slot"]}-{row["id"]}.json' for row in records]
        by_path = {finish.STATE / name: row for name, row in zip(names, records)}
        def directory(names):
            context = Mock()
            context.__enter__ = Mock(return_value=iter(SimpleNamespace(name=name) for name in names))
            context.__exit__ = Mock(return_value=False)
            return context
        with patch.object(finish, 'root_directory'), \
             patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
             patch.object(finish.os, 'scandir', return_value=directory(['manifest.json', *names])) as listing, \
             patch.object(finish, 'read_public_json', side_effect=lambda path, _: by_path[path]) as read:
            self.assertEqual(finish.read_cleanup_receipts(m), records)
            listing.assert_called_once_with(finish.STATE)
            self.assertEqual([call.args for call in read.call_args_list],
                             [(finish.STATE / name, 4096) for name in names])
        for filename in ('cleanup-1-201.json.tmp', 'cleanup-5-201.json', 'cleanup-1-201-seed.json'):
            with patch.object(finish, 'root_directory'), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
                 patch.object(finish.os, 'scandir', return_value=directory([filename])), \
                 patch.object(finish, 'read_public_json') as read:
                with self.subTest(filename=filename), self.assertRaises(RuntimeError): finish.read_cleanup_receipts(m)
                read.assert_not_called()
        with patch.object(finish, 'root_directory'), \
             patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o40700)), \
             patch.object(finish.os, 'scandir', side_effect=PermissionError):
            with self.assertRaises(PermissionError): finish.read_cleanup_receipts(m)

    def test_every_parent_root_nofollow_and_sticky_nix_store_exception_only(self):
        root = SimpleNamespace(st_mode=0o40755, st_uid=0, st_gid=0)
        with patch.object(Path, 'lstat', return_value=root): finish.root_directory(Path('/var/lib/state'))
        for meta in (SimpleNamespace(st_mode=0o40777, st_uid=0, st_gid=0),
                     SimpleNamespace(st_mode=0o40700, st_uid=1000, st_gid=100),
                     SimpleNamespace(st_mode=0o120777, st_uid=0, st_gid=0)):
            with patch.object(Path, 'lstat', return_value=meta), self.assertRaises(RuntimeError):
                finish.root_directory(Path('/var/lib/state'))
        def meta(path):
            return SimpleNamespace(st_mode=0o41775, st_uid=0, st_gid=30000) if path == Path('/nix/store') else root
        with patch.object(Path, 'lstat', autospec=True, side_effect=meta): finish.root_directory(Path('/nix/store'))
        with self.assertRaises(RuntimeError): finish.root_directory(Path('relative'))

    def test_rootfile0600_regular_singlelink_and_read_identity_checked(self):
        data = b'{"id":1}'
        meta = SimpleNamespace(st_mode=0o100600, st_uid=0, st_gid=0, st_size=len(data), st_nlink=1,
                               st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
        with patch.object(finish, 'root_directory'), patch.object(finish.os, 'open', return_value=123) as open_fd, \
             patch.object(finish.os, 'close'), patch.object(finish.os, 'fstat', return_value=meta), \
             patch.object(finish.os, 'fdopen', return_value=io.BytesIO(data)), patch.object(Path, 'lstat', return_value=meta):
            self.assertEqual(finish.read_public_json('/var/lib/state/proof.json', 4096), {'id': 1})
            self.assertTrue(open_fd.call_args.args[1] & finish.os.O_NOFOLLOW)
        for key, value in [('st_uid', 1000), ('st_gid', 100), ('st_mode', 0o100644),
                           ('st_mode', 0o040600), ('st_nlink', 2), ('st_size', 5000)]:
            corrupted = copy.copy(meta); setattr(corrupted, key, value)
            with patch.object(finish, 'root_directory'), patch.object(finish.os, 'open', return_value=123), \
                 patch.object(finish.os, 'close'), patch.object(finish.os, 'fstat', return_value=corrupted):
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    finish.read_public_json('/var/lib/state/proof.json', 4096)

    def test_immutable_direct_source_sha_and_root_byte_read(self):
        path = Path('/nix/store/source.py'); data = b'# source\n'
        with patch.object(Path, 'resolve', return_value=path), patch.object(finish, 'read_root_bytes', return_value=data):
            self.assertEqual(finish.validate_operator_source(path, finish.digest(data)), data)
            with self.assertRaises(RuntimeError): finish.validate_operator_source(path, 'a' * 64)
        with self.assertRaises(RuntimeError): finish.validate_operator_source(Path('/tmp/source.py'), 'a' * 64)
        with patch.object(Path, 'resolve', return_value=Path('/nix/store/other.py')):
            with self.assertRaises(RuntimeError): finish.validate_operator_source(path, 'a' * 64)


if __name__ == '__main__': unittest.main()
