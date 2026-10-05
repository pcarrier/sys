#!/usr/bin/env python3
"""Host-free activation tests. Never query systemd, root files, mounts or APIs.

The synthetic final-validator is a CONTRACT fixture, not proof that the parent's
finish-drain.py validates real journal/Actions evidence. Integration stays held
until that immutable pinned validator and schema-2 backup actually exist.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('activate', Path(__file__).with_name('activate-cache-v2.py'))
act = importlib.util.module_from_spec(spec)
spec.loader.exec_module(act)
spec_effect = importlib.util.spec_from_file_location('effect', Path(__file__).with_name('effect-proof.py'))
effect = importlib.util.module_from_spec(spec_effect)
spec_effect.loader.exec_module(effect)


def is_start(argv):
    return argv[:3] == ['systemctl', '--job-mode=fail', 'start']


def effect_unit(name, state='active', **changes):
    result = {key: [] for key in effect.UNIT_ARRAYS}
    result.update({key: '' for key in effect.UNIT_STRINGS})
    result.update({key: False for key in effect.UNIT_BOOLS})
    result.update(Id=name, Names=[name], FollowingSet=[], LoadState='loaded', ActiveState=state,
                  SubState='dead' if state == 'inactive' else 'running', FreezerState='running',
                  Job=[0, '/'], FailureAction='none', SuccessAction='none',
                  StartLimitAction='none', JobTimeoutAction='none', LoadError=['', ''])
    result.update(changes)
    return result


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def command(slot, new=False):
    result = ['/nix/store/' + ('new' if new else 'old') + '-hound-ci/bin/hound-ci',
              'worker', '--slot', str(slot), '--repo', 'xmit-dev/ultimator',
              '--guest', '/nix/store/' + ('new' if new else 'old') + '-guest.sh']
    return result + (['--image', 'base-cache-v2.qcow2'] if new else [])


def unit_data(slot, new=False):
    return ('[Unit]\nDescription=fixture\n[Service]\nRestart=always\nExecStart=' +
            ' '.join(command(slot, new)) + '\n').encode()


def original_invocation(slot):
    return f'{slot:x}' * 32


def new_invocation(slot):
    return f'{slot + 10:x}' * 32


def loaded(slot, new=False, held=True, invocation=None):
    name = f'hound-ci-{slot}.service'
    argv = command(slot, new)
    return {'Id': name, 'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
            'MainPID': '0', 'Restart': 'no' if held else 'always', 'ControlGroup': '',
            'InvocationID': invocation or original_invocation(slot),
            'Slice': 'hound-ci.slice', 'Requires': ' '.join(sorted(act.DEPENDENCIES - {'hound-ci.slice'})),
            'Wants': '', 'Requisite': '', 'BindsTo': '',
            'FragmentPath': str(act.ATTACHED / name),
            'DropInPaths': [str(act.RUNTIME / (name + '.d') / act.DROPIN)] if held else [],
            'ExecStart': [[argv[0], argv, False, 0, 0, 0, 0, 0, 0, 0]]}


class FixtureValidator:
    """Checks explicit evidence fields to prove activation delegates failures."""
    ACTIVATION_TRANSITION_API = act.TRANSITION_API

    def __init__(self):
        self.rechecks = []
        self.reject_phase = None

    def validate_certificate(self, manifest):
        act.require(manifest.get('certificate_schema') == 'fixture-final-v1', 'Malformed final certificate')
        for entry in manifest['controllers']:
            item = manifest['drain_witness'][str(entry['slot'])]
            act.require(item['old_pid'] == entry['pid'] and item['starttime'] == entry['starttime'],
                        'Malformed PID/starttime evidence')
            act.require(item.get('latest_vm', {}).get('name') == f'fixture-vm-{entry["slot"]}' and
                        int(item['manager_monotonic']) > int(item['latest_vm_monotonic']),
                        'Malformed latestVM/manager evidence')
            job = item.get('actions_job')
            act.require(isinstance(job, dict) and job.get('status') == 'completed' and
                        type(job.get('id')) is int and job['id'] > 0,
                        'Missing terminal Actions evidence')
            act.require(item.get('registration_witness') == {'kind': 'fixture-removed'},
                        'Missing registration evidence')

    def revalidate_final(self, drain, manifest):
        self.validate_certificate(manifest)
        self.rechecks.append(drain.activation_phase)
        act.require(drain.activation_phase != self.reject_phase, 'Original evidence drift')
        for slot in act.UNITS:
            values = drain.properties(slot)
            tracked = drain.activation_new_controllers.get(slot)
            expected_pid = str(tracked['pid']) if tracked else '0'
            act.require(values['MainPID'] == expected_pid and values['Restart'] == drain.expected_restart[slot],
                        'Actual loaded stopped/hold or tracked-new state drift')


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.paths = {name: self.root / name.lower() for name in
                      ('STATE', 'BACKUP', 'ATTACHED', 'RUNTIME', 'GCROOTS', 'CGROUPS')}
        self.paths['ENABLE'] = self.paths['ATTACHED'] / 'multi-user.target.wants'
        for path in self.paths.values():
            path.mkdir(parents=True, exist_ok=True)
        for name in ('STATE', 'BACKUP'):
            self.paths[name].chmod(0o700)
        profile = self.root / 'profile'
        profile.mkdir()
        self.paths['CURRENT'] = self.root / 'current-system'
        self.paths['CURRENT'].symlink_to(profile)
        for name, content in (('CANDIDATE', b'candidate'), ('OLD_IMAGE', b'old-pristine')):
            self.paths[name] = self.root / name.lower()
            self.paths[name].write_bytes(content)
            self.paths[name].chmod(0o444)
        self.stack = ExitStack()
        for name, path in self.paths.items():
            self.stack.enter_context(patch.object(act, name, path))
        self.stack.enter_context(patch.object(act, 'CANDIDATE_SHA', hash_bytes(b'candidate')))
        self.stack.enter_context(patch.object(act, 'OLD_SHA', hash_bytes(b'old-pristine')))
        # Fixtures belong to the test's normal user. Model only ownership,
        # keeping REAL modes/types/descriptors/no-follow/hash/fsync/flock.
        original_fstat, original_lstat = os.fstat, Path.lstat
        def owner(meta):
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
                      'st_mode', 'st_nlink')
            return SimpleNamespace(**{field: getattr(meta, field) for field in fields}, st_uid=0, st_gid=0)
        self.stack.enter_context(patch.object(act.os, 'fstat', side_effect=lambda fd: owner(original_fstat(fd))))
        self.stack.enter_context(patch.object(act.Path, 'lstat', autospec=True,
                                             side_effect=lambda path: owner(original_lstat(path))))
        self.store = {}
        self.stack.enter_context(patch.object(act, 'store_file', side_effect=lambda path: self.store[str(path)]))
        self.validator = FixtureValidator()
        self.operator = SimpleNamespace(STATE=act.STATE, starttime=lambda pid: str(900000 + pid))
        self.manifest = {'drain_nonce': 'a' * 32, 'certificate_schema': 'fixture-final-v1', 'phase': 'all-four-drained-awaiting-replacement',
                         'validator_source': '/nix/store/validator.py', 'validator_sha256': 'a' * 64,
                         'operator_source': '/nix/store/operator.py', 'operator_sha256': 'b' * 64,
                         'waiter_source': '/nix/store/waiter.py', 'waiter_sha256': 'c' * 64,
                         'gate': '/nix/store/gate.py', 'gate_sha256': 'd' * 64,
                         'controllers': [], 'drain_witness': {}}
        self.backup = {'schema': 2, 'host_profile': str(profile), 'host_profile_resolved': str(profile),
                       'old_image': {'path': str(act.OLD_IMAGE), 'sha256': act.OLD_SHA},
                       'old_unit_links': [], str(act.GCROOTS): [], str(act.ENABLE): []}
        self.manager = {f'hound-ci-{slot}.service': loaded(slot) for slot in act.UNITS}
        self.dependencies = {name: {'Id': name, 'LoadState': 'loaded', 'ActiveState': 'active',
                                    'Requires': '', 'Wants': '', 'Requisite': '', 'BindsTo': ''}
                             for name in act.DEPENDENCIES}
        self.commands = []
        for slot in act.UNITS:
            name = f'hound-ci-{slot}.service'
            target = f'/nix/store/old-unit-{slot}/{name}'
            self.store[target] = unit_data(slot)
            self.store[str(Path(act.UNITS[slot]) / name)] = unit_data(slot, True)
            for argv in (command(slot), command(slot, True)):
                self.store[argv[0]], self.store[argv[7]] = b'controller fixture', b'guest fixture'
            (act.ATTACHED / name).symlink_to(target)
            copy = act.BACKUP / name
            copy.write_bytes(unit_data(slot))
            copy.chmod(0o600)
            entry = {'unit': name, 'path': str(act.ATTACHED / name), 'target': target,
                     'uid': 0, 'gid': 0, 'sha256': hash_bytes(unit_data(slot)), 'backup': str(copy)}
            self.backup['old_unit_links'].append(entry)
            for folder in (act.GCROOTS, act.ENABLE):
                link = folder / name
                link.symlink_to(target.rsplit('/', 1)[0] if folder == act.GCROOTS else '../' + name)
                self.backup[str(folder)].append({'path': str(link), 'target': os.readlink(link), 'uid': 0, 'gid': 0})
            hold = act.RUNTIME / (name + '.d') / act.DROPIN
            hold.parent.mkdir()
            hold.write_bytes(act.HOLD)
            hold.chmod(0o644)
            self.manifest['controllers'].append({'slot': slot, 'pid': 100 + slot, 'starttime': str(200 + slot),
                                                 'invocation_id': original_invocation(slot),
                                                 'control_group': f'/hound.slice/hound-ci.slice/{name}'})
            self.manifest['drain_witness'][str(slot)] = {
                'old_pid': 100 + slot, 'starttime': str(200 + slot), 'cgroup_removed': True,
                'latest_vm': {'name': f'fixture-vm-{slot}'}, 'latest_vm_monotonic': '300',
                'manager_monotonic': '400', 'actions_job': {'id': slot, 'status': 'completed'},
                'registration_witness': {'kind': 'fixture-removed'},
            }
        self.write_manifests()
        self.stack.enter_context(patch.object(act, 'properties', side_effect=self.properties))
        self.stack.enter_context(patch.object(act, 'no_queued_jobs'))
        self.jobs = []
        self.stack.enter_context(patch.object(act, 'queued_jobs', side_effect=lambda: deepcopy(self.jobs)))
        self.stack.enter_context(patch.object(act, 'loaded_index', side_effect=lambda: {name: {} for name in self.manager | self.dependencies}))
        self.stack.enter_context(patch.object(act, 'effect_metadata', side_effect=self.effect_metadata))
        self.stack.enter_context(patch.object(act, 'device_sysfs_snapshot', return_value={}))
        self.manager_identity = {'version': '261.2', 'executable': '/nix/store/systemd/lib/systemd/systemd', 'sha256': 'f' * 64}
        self.stack.enter_context(patch.object(act, 'current_manager_identity', side_effect=lambda: deepcopy(self.manager_identity)))
        self.stack.enter_context(patch.object(act, 'run', side_effect=self.run))
        self.stack.enter_context(patch.object(act.os, 'geteuid', return_value=0))
        # Host-namespace preflight is simulated, never inspect /proc/1.
        original_stat = os.stat
        def namespace_stat(path, *args, **kwargs):
            if str(path) in ('/proc/self/ns/mnt', '/proc/1/ns/mnt'):
                return SimpleNamespace(st_ino=1)
            return original_stat(path, *args, **kwargs)
        self.stack.enter_context(patch.object(act.os, 'stat', side_effect=namespace_stat))
        self.stack.enter_context(patch.object(act, 'direct_source', return_value=b'fixture pinned source'))
        self.stack.enter_context(patch.object(act, 'load_source', side_effect=self.source))
        self.ack = 'https://ultimator.app/sessions/parentfixture?at=42'
        self.activation_sha = hash_bytes(b'fixture pinned source')
        self.effect_source = Path('/nix/store/effect.py')
        self.effect_sha = 'e' * 64
        terminal = act.STATE / 'actions-terminal.json'
        terminal.write_text('{"fixture":"terminal"}')
        terminal.chmod(0o600)
        proofs = {name: effect.prove(name, lambda name: self.effect_metadata(name)) for name in self.manager}
        self.window = {'schema': 1, 'kind': act.WINDOW_KIND, 'status': 'held',
            'drain_nonce': self.manifest['drain_nonce'], 'manifest_sha256': act.sha256(act.canonical(self.manifest)),
            'terminal_certificate_sha256': hash_bytes(terminal.read_bytes()),
            'activation_source': act.__file__, 'activation_sha256': self.activation_sha,
            'effect_source': str(self.effect_source), 'effect_sha256': self.effect_sha, 'effect_schema': effect.SCHEMA,
            'effect_structure_sha256': act.effect_structure(proofs), 'manager_identity': self.manager_identity,
            'manager_source_review': {'ack': self.ack, 'source_patch_sha256': '0' * 64, 'primary_v261_sha256': effect.SOURCE_SHA256},
            'parent_ack': self.ack, 'resources': {'units': list(self.manager), 'state': str(act.STATE),
                'backup': str(act.BACKUP), 'attached': str(act.ATTACHED), 'runtime': str(act.RUNTIME),
                'gcroots': str(act.GCROOTS), 'enable': str(act.ENABLE), 'profile': str(act.CURRENT),
                'candidate': str(act.CANDIDATE), 'old_image': str(act.OLD_IMAGE)},
            'held_since_utc': '2026-10-05T00:00:00+00:00', 'nominal_minutes': 5,
            'release_rule': act.RELEASE_RULE, 'cooperation': ['systemd-control-requests',
                'unit-files-and-load-cache', 'ci-registration-and-rollout-state'], 'ignored_not_found': []}
        self.write_window()

    def write_manifests(self):
        for path, value in ((act.STATE / 'manifest.json', self.manifest),
                            (act.BACKUP / 'rollback-manifest.json', self.backup)):
            path.write_text(json.dumps(value))
            path.chmod(0o600)

    def write_window(self):
        path = act.STATE / 'control-window.json'
        path.write_text(json.dumps(self.window))
        path.chmod(0o600)
        self.window_sha = hash_bytes(path.read_bytes())

    def effect_metadata(self, name, *unused):
        values = self.properties(name)
        result = effect_unit(name, values['ActiveState'])
        for key in ('LoadState', 'SubState'):
            if key in values:
                result[key] = values[key]
        for key in effect.RELATIONS:
            if key in values:
                result[key] = shlex.split(values[key]) if isinstance(values[key], str) else values[key]
        if name in self.manager and 'hound-ci.slice' not in result['Requires']:
            result['Requires'].append('hound-ci.slice')
        return result

    def properties(self, name):
        return deepcopy(self.manager[name] if name in self.manager else self.dependencies[name])

    def source(self, path, digest, name):
        if 'effect' in name:
            return effect
        return self.validator if 'validator' in name else self.operator

    def run(self, argv):
        self.commands.append(argv)
        if argv == ['systemctl', 'daemon-reload']:
            for slot in act.UNITS:
                name = f'hound-ci-{slot}.service'
                new = os.readlink(act.ATTACHED / name) == str(Path(act.UNITS[slot]) / name)
                held = (act.RUNTIME / (name + '.d') / act.DROPIN).exists()
                # daemon-reload re-serializes; the dead unit keeps its last invocation.
                self.manager[name] = loaded(slot, new, held, self.manager[name]['InvocationID'])
        elif is_start(argv):
            assert len(argv) == 5 and argv[3] == '--'
            name = argv[4]
            slot = next(slot for slot in act.UNITS if name == f'hound-ci-{slot}.service')
            self.manager[name].update(MainPID=str(500 + slot), ActiveState='active', SubState='running',
                                     ControlGroup=f'/hound.slice/hound-ci.slice/{name}',
                                     InvocationID=new_invocation(slot))
        else:
            raise AssertionError(f'Unapproved command in fixture: {argv}')
        return ''

    def activate(self, source=None):
        act.activate(Path(source or self.manifest['validator_source']), self.manifest['validator_sha256'],
                     activation_sha=self.activation_sha, effect_source=self.effect_source, effect_sha=self.effect_sha,
                     control_window=act.STATE / 'control-window.json', control_window_sha=self.window_sha, parent_ack=self.ack)

    def receipt(self):
        return json.loads((act.STATE / 'activation.json').read_text())

    def close(self):
        self.stack.close()


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.temp.name)

    def tearDown(self):
        self.fixture.close()
        self.temp.cleanup()

    def test_full_one_shot_transition_and_every_mutation_has_intent_completion(self):
        self.fixture.activate()
        receipt = self.fixture.receipt()
        events = receipt['events']
        self.assertEqual(len(events), 19)
        self.assertTrue(all(event['intent_utc'] and event['completion_utc'] for event in events))
        self.assertEqual([command[:2] for command in self.fixture.commands],
                         [['systemctl', 'daemon-reload'], ['systemctl', 'daemon-reload']] +
                         [['systemctl', '--job-mode=fail']] * 4)
        self.assertEqual(len(self.fixture.validator.rechecks), 2 * len(events) + 2)
        self.assertEqual(receipt['phase'], 'new-four-started-awaiting-runtime-proof')
        for entry in self.fixture.backup['old_unit_links']:
            self.assertEqual(act.read_file(entry['backup'], mode=0o600), self.fixture.store[entry['target']])
        self.assertEqual(act.OLD_IMAGE.read_bytes(), b'old-pristine')
        with self.assertRaisesRegex(RuntimeError, 'Prior activation'):
            self.fixture.activate()

    def test_malformed_final_certificate_is_delegated_and_never_mutates(self):
        for field, value in (('actions_job', None), ('registration_witness', None),
                             ('manager_monotonic', '100'), ('starttime', 'reused'), ('latest_vm', {})):
            with self.subTest(field=field):
                original = deepcopy(self.fixture.manifest)
                self.fixture.manifest['drain_witness']['1'][field] = value
                self.fixture.write_manifests()
                with self.assertRaises(RuntimeError):
                    self.fixture.activate()
                self.assertFalse((act.STATE / 'activation.json').exists())
                self.assertEqual(self.fixture.commands, [])
                self.fixture.manifest = original

    def test_negative_validator_return_is_fail_closed(self):
        with patch.object(self.fixture.validator, 'validate_certificate', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'rejected certificate'):
                self.fixture.activate()
        with patch.object(self.fixture.validator, 'revalidate_final', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'rejected final revalidation'):
                self.fixture.activate()
        self.assertFalse((act.STATE / 'activation.json').exists())
        self.assertEqual(self.fixture.commands, [])

    def test_summary_booleans_are_not_a_final_certificate(self):
        self.fixture.manifest.pop('certificate_schema')
        self.fixture.manifest['drain_witness'] = {str(slot): {'controller_exited': True,
            'cgroup_empty': True, 'completed_vm': True, 'drained': True} for slot in act.UNITS}
        self.fixture.write_manifests()
        with self.assertRaisesRegex(RuntimeError, 'Malformed final'):
            self.fixture.activate()
        self.assertFalse((act.STATE / 'activation.json').exists())

    def test_validator_cli_pin_and_all_source_provenance_are_required(self):
        with self.assertRaisesRegex(RuntimeError, 'validator pin'):
            self.fixture.activate('/nix/store/other.py')
        self.fixture.manifest.pop('waiter_source')
        self.fixture.write_manifests()
        with self.assertRaisesRegex(RuntimeError, 'source provenance'):
            self.fixture.activate()

    def test_actual_loaded_cgroup_drift_fails_before_any_mutation(self):
        self.fixture.manager['hound-ci-1.service']['ControlGroup'] = '/wrong.slice/empty'
        with self.assertRaisesRegex(RuntimeError, 'ControlGroup drift'):
            self.fixture.activate()
        self.assertEqual(self.fixture.commands, [])

    def test_removed_path_requires_original_removal_certificate(self):
        self.fixture.manifest['drain_witness']['1']['cgroup_removed'] = False
        self.fixture.write_manifests()
        self.fixture.window['manifest_sha256'] = act.sha256(act.canonical(self.fixture.manifest))
        self.fixture.write_window()
        with self.assertRaisesRegex(RuntimeError, 'ORIGINAL-cgroup removal'):
            self.fixture.activate()

    def test_populated_descendants_block_even_with_empty_direct_procs(self):
        group = act.CGROUPS / 'hound.slice/hound-ci.slice/hound-ci-1.service'
        group.mkdir(parents=True)
        (group / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
        (group / 'cgroup.procs').write_text('')
        self.fixture.manager['hound-ci-1.service']['ControlGroup'] = '/hound.slice/hound-ci.slice/hound-ci-1.service'
        with self.assertRaisesRegex(RuntimeError, 'descendants populated'):
            self.fixture.activate()

    def test_loaded_command_and_unit_shadowing_are_not_substring_checks(self):
        name = 'hound-ci-1.service'
        original = deepcopy(self.fixture.manager[name])
        changes = [
            ('FragmentPath', '/etc/systemd/system/hound-ci-1.service'),
            ('DropInPaths', [str(act.RUNTIME / (name + '.d') / act.DROPIN), '/etc/systemd/system/x.conf']),
            ('ExecStart', [['/nix/store/evil', command(1), False, 0, 0, 0, 0, 0, 0, 0]]),
            ('ExecStart', [[command(1)[0], command(1) + ['--image', 'base-cache-v2.qcow2'], False, 0, 0, 0, 0, 0, 0, 0]]),
            ('ExecStart', [[command(1)[0], command(1), True, 0, 0, 0, 0, 0, 0, 0]]),
            ('ExecStart', '{ path=evil ; argv[]=old --image base-cache-v2.qcow2 ; }'),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.fixture.manager[name] = deepcopy(original)
                self.fixture.manager[name][key] = value
                with self.assertRaises(RuntimeError):
                    self.fixture.activate()
                self.assertEqual(self.fixture.commands, [])
        self.fixture.manager[name] = original

    def test_existing_dependency_must_be_active_without_repair(self):
        for name in act.DEPENDENCIES:
            with self.subTest(name=name):
                self.fixture.dependencies[name]['ActiveState'] = 'inactive'
                with self.assertRaisesRegex(RuntimeError, 'anchored nonanchor'):
                    self.fixture.activate()
                self.assertEqual(self.fixture.commands, [])
                self.assertFalse((act.STATE / 'activation.json').exists())
                self.fixture.dependencies[name]['ActiveState'] = 'active'

    def test_transitive_inactive_dag_is_not_repaired_but_full_queue_blocks(self):
        self.fixture.dependencies['hound-ci-image.service']['Wants'] = 'unexpected.service'
        self.fixture.dependencies['unexpected.service'] = {
            'Id': 'unexpected.service', 'LoadState': 'loaded', 'ActiveState': 'inactive',
            'Requires': '', 'Wants': '', 'Requisite': '', 'BindsTo': ''}
        self.fixture.jobs = [[1, 'unexpected.service', 'reload', 'waiting', '/job', '/unit']]
        with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
            self.fixture.activate()
        self.assertFalse((act.STATE / 'activation.json').exists())

    def test_evidence_revalidated_before_hold_removal(self):
        self.fixture.validator.reject_phase = 'hold-remove'
        with self.assertRaisesRegex(RuntimeError, 'evidence drift'):
            self.fixture.activate()
        for slot in act.UNITS:
            self.assertTrue((act.RUNTIME / (f'hound-ci-{slot}.service.d') / act.DROPIN).exists())
        self.assertEqual(self.fixture.commands, [['systemctl', 'daemon-reload']])

    def test_profile_revalidated_before_each_mutation(self):
        original = self.fixture.validator.revalidate_final
        def drift(drain, manifest):
            original(drain, manifest)
            if drain.activation_phase == 'hold-remove':
                act.CURRENT.unlink()
                act.CURRENT.symlink_to(self.fixture.root / 'drifted')
        with patch.object(self.fixture.validator, 'revalidate_final', side_effect=drift):
            with self.assertRaisesRegex(RuntimeError, 'profile drifted'):
                self.fixture.activate()
        self.assertFalse(any(argv[1] == 'start' for argv in self.fixture.commands))

    def test_backup_requires_complete_schema_and_original_content(self):
        original = deepcopy(self.fixture.backup)
        changes = [lambda value: value.update(schema=1),
                   lambda value: value['old_unit_links'][0].update(sha256='f' * 64),
                   lambda value: value['old_unit_links'][0].update(target='/nix/store/unknown'),
                   lambda value: value.update(old_image={'path': str(act.OLD_IMAGE), 'sha256': 'f' * 64}),
                   lambda value: value.pop(str(act.GCROOTS))]
        for change in changes:
            self.fixture.backup = deepcopy(original)
            change(self.fixture.backup)
            self.fixture.write_manifests()
            with self.assertRaises((RuntimeError, KeyError)):
                self.fixture.activate()
            self.assertEqual(self.fixture.commands, [])
        self.fixture.backup = original
        copy = Path(original['old_unit_links'][0]['backup'])
        copy.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'content hash/copy'):
            self.fixture.activate()

    def test_old_gc_roots_and_enablelinks_are_exact_and_retained(self):
        for folder in (act.GCROOTS, act.ENABLE):
            link = folder / 'hound-ci-1.service'
            previous = os.readlink(link)
            link.unlink()
            link.symlink_to('/nix/store/wrong')
            with self.assertRaisesRegex(RuntimeError, 'target/link ownership drift'):
                self.fixture.activate()
            link.unlink()
            link.symlink_to(previous)

    def test_old_image_missing_or_hash_drift_blocks(self):
        act.OLD_IMAGE.chmod(0o644)
        act.OLD_IMAGE.write_bytes(b'corrupt')
        act.OLD_IMAGE.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError, 'image SHA changed'):
            self.fixture.activate()

    def test_immutable_candidate_must_be_regular_no_follow_and_hash_bound(self):
        original = act.CANDIDATE.read_bytes()
        act.CANDIDATE.unlink()
        target = self.fixture.root / 'other-image'
        target.write_bytes(original)
        target.chmod(0o444)
        act.CANDIDATE.symlink_to(target)
        with self.assertRaises(OSError):
            self.fixture.activate()
        act.CANDIDATE.unlink()
        act.CANDIDATE.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'regular descriptor'):
            self.fixture.activate()

    def test_pinned_descriptor_detects_file_replacement(self):
        pin = act.ImagePin(act.CANDIDATE, act.CANDIDATE_SHA)
        try:
            replacement = self.fixture.root / 'replacement'
            replacement.write_bytes(b'candidate')
            replacement.chmod(0o444)
            os.replace(replacement, act.CANDIDATE)
            with self.assertRaisesRegex(RuntimeError, 'descriptor drifted'):
                pin.check()
        finally:
            pin.close()

    def test_root600_bounded_json_no_follow_and_duplicate_keys(self):
        path = act.STATE / 'test.json'
        path.write_text('{"a":1,"a":2}')
        path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            act.read_public_json(path)
        path.write_text('{"a":1}')
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'Wrong file mode'):
            act.read_public_json(path)
        path.unlink()
        path.symlink_to(act.STATE / 'manifest.json')
        with self.assertRaises(OSError):
            act.read_public_json(path)
        path.unlink()
        path.write_bytes(b'x' * (act.JSON_LIMIT + 1))
        path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'bound exceeded'):
            act.read_public_json(path)

    def test_concurrent_invocations_are_exclusively_locked(self):
        outcome = []
        with act.exclusive_lock():
            def attempt():
                try:
                    with act.exclusive_lock():
                        outcome.append('unsafe acquired')
                except RuntimeError as error:
                    outcome.append(str(error))
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIn('exclusive lock', outcome[0])

    def test_interrupted_atomic_replace_keeps_intent_no_rollback_or_restart(self):
        original_replace = os.replace
        def interrupted(source, destination):
            original_replace(source, destination)
            if str(destination) == str(act.ATTACHED / 'hound-ci-1.service'):
                raise KeyboardInterrupt('fixture interruption AFTER rename')
        with patch.object(act.os, 'replace', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.fixture.activate()
        last = self.fixture.receipt()['events'][-1]
        self.assertEqual(last['operation'], 'unit-link-replace')
        self.assertIsNone(last['completion_utc'])
        self.assertEqual(os.readlink(act.ATTACHED / 'hound-ci-1.service'),
                         str(Path(act.UNITS[1]) / 'hound-ci-1.service'))
        self.assertEqual(self.fixture.commands, [])
        with self.assertRaisesRegex(RuntimeError, 'Prior activation'):
            self.fixture.activate()

    def test_reload_and_start_failure_have_durable_intents_no_retry(self):
        self.fixture.validator.reject_phase = 'reload-new-held'
        with self.assertRaises(RuntimeError):
            self.fixture.activate()
        self.assertEqual(self.fixture.commands, [])
        self.assertEqual(self.fixture.receipt()['events'][-1]['operation'], 'unit-link-replace')
        self.assertTrue(all((act.RUNTIME / (f'hound-ci-{slot}.service.d') / act.DROPIN).exists()
                            for slot in act.UNITS))

    def test_reload_command_failure_is_durable_and_not_retried(self):
        original = self.fixture.run
        def fail(argv):
            if argv == ['systemctl', 'daemon-reload']:
                self.fixture.commands.append(argv)
                raise RuntimeError('fixture daemon-reload failed')
            return original(argv)
        with patch.object(act, 'run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'daemon-reload failed'):
                self.fixture.activate()
        self.assertEqual(self.fixture.commands, [['systemctl', 'daemon-reload']])
        last = self.fixture.receipt()['events'][-1]
        self.assertEqual(last['operation'], 'reload-new-held')
        self.assertIsNone(last['completion_utc'])
        self.assertTrue(all((act.RUNTIME / (f'hound-ci-{slot}.service.d') / act.DROPIN).exists()
                            for slot in act.UNITS))

    def test_partial_start_failure_keeps_intent_and_never_rolls_back(self):
        original = self.fixture.run
        def fail(argv):
            if is_start(argv):
                self.fixture.commands.append(argv)
                self.fixture.manager['hound-ci-1.service'].update(
                    MainPID='501', ActiveState='active', SubState='running',
                    ControlGroup='/hound.slice/hound-ci.slice/hound-ci-1.service')
                raise RuntimeError('fixture partial start failed')
            return original(argv)
        with patch.object(act, 'run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'partial start failed'):
                self.fixture.activate()
        last = self.fixture.receipt()['events'][-1]
        self.assertEqual(last['operation'], 'start-anchor')
        self.assertIsNone(last['completion_utc'])
        self.assertEqual(self.fixture.manager['hound-ci-1.service']['MainPID'], '501')
        self.assertEqual(sum(is_start(argv) for argv in self.fixture.commands), 1)
        self.assertFalse(any('stop' in argv or 'reset-failed' in argv for argv in self.fixture.commands))
        with self.assertRaisesRegex(RuntimeError, 'Prior activation'):
            self.fixture.activate()

    def test_dependency_drift_before_start_does_not_start_or_repair(self):
        original = self.fixture.validator.revalidate_final
        def drift(drain, manifest):
            original(drain, manifest)
            if drain.activation_phase == 'start-anchor':
                self.fixture.dependencies['hound-ci-image.service']['ActiveState'] = 'failed'
        with patch.object(self.fixture.validator, 'revalidate_final', side_effect=drift):
            with self.assertRaisesRegex(RuntimeError, 'anchored nonanchor'):
                self.fixture.activate()
        self.assertEqual(self.fixture.commands,
                         [['systemctl', 'daemon-reload'], ['systemctl', 'daemon-reload']])

    def test_disk_fsync_failure_keeps_intent_and_no_retry(self):
        original_sync = act.fsync_dir
        def failure(path):
            if Path(path) == act.GCROOTS:
                raise OSError('fixture disk sync failure')
            return original_sync(path)
        with patch.object(act, 'fsync_dir', side_effect=failure):
            with self.assertRaisesRegex(OSError, 'disk sync failure'):
                self.fixture.activate()
        last = self.fixture.receipt()['events'][-1]
        self.assertEqual(last['operation'], 'root-namespace-create')
        self.assertIsNone(last['completion_utc'])
        self.assertEqual(self.fixture.commands, [])

    def test_missing_transition_api_holds_before_any_persistent_change(self):
        with patch.object(self.fixture.validator, 'ACTIVATION_TRANSITION_API', None):
            with self.assertRaisesRegex(RuntimeError, 'Integration HOLD'):
                self.fixture.activate()
        self.assertEqual(self.fixture.commands, [])
        self.assertFalse((act.STATE / 'activation.json').exists())

    def test_every_individual_start_has_fail_mode_boundary_and_fresh_post_intent(self):
        self.fixture.activate()
        starts = [event for event in self.fixture.receipt()['events'] if event['operation'] == 'start-anchor']
        self.assertEqual([event['details']['argv'] for event in starts],
                         [['systemctl', '--job-mode=fail', 'start', '--', f'hound-ci-{slot}.service']
                          for slot in act.UNITS])
        self.assertEqual(self.fixture.validator.rechecks.count('start-anchor'), 4)
        self.assertEqual(self.fixture.validator.rechecks.count('start-anchor-post-intent'), 4)
        self.assertEqual(set(self.fixture.receipt()['new_controllers']), {'1', '2', '3', '4'})
        self.assertEqual(self.fixture.receipt()['cooperative_window'], 'HELD-no-auto-release')
        self.assertEqual(json.loads((act.STATE / 'control-window.json').read_text())['status'], 'held')

    def test_unknown_start_of_unstarted_slot_holds_before_any_mutation(self):
        # Any start (manual, dependency, auto-restart) acquires a new invocation
        # even when the unit is dead again by the time activation looks.
        self.fixture.manager['hound-ci-3.service']['InvocationID'] = 'f' * 32
        with self.assertRaisesRegex(RuntimeError, 'lost its ORIGINAL invocation'):
            self.fixture.activate()
        self.assertEqual(self.fixture.commands, [])
        self.assertFalse((act.GCROOTS / act.ROOT_NAME).exists())

    def test_root_durable_slot_start_intent_precedes_dispatch_and_result_binds_new_invocation(self):
        seen = []
        original = self.fixture.run
        def observe(argv):
            if is_start(argv):
                slot = int(argv[4].split('-')[2].split('.')[0])
                record = self.fixture.receipt()['slot_starts'][str(slot)]
                seen.append((slot, record['stage'], record['result'], record['pre_start']['invocation_id']))
            return original(argv)
        with patch.object(act, 'run', side_effect=observe):
            self.fixture.activate()
        self.assertEqual(seen, [(slot, 'start-intent', None, original_invocation(slot)) for slot in act.UNITS])
        starts = self.fixture.receipt()['slot_starts']
        for slot in act.UNITS:
            record = starts[str(slot)]
            self.assertEqual(record['stage'], 'started')
            self.assertEqual(record['request'], ['systemctl', '--job-mode=fail', 'start', '--', f'hound-ci-{slot}.service'])
            self.assertEqual(record['source'], str(Path(act.UNITS[slot]) / f'hound-ci-{slot}.service'))
            self.assertEqual(record['argv'], command(slot, True))
            self.assertEqual(record['image'], {'path': str(act.CANDIDATE), 'sha256': act.CANDIDATE_SHA})
            self.assertEqual(record['pre_start']['original_cgroup'], 'removed')
            self.assertEqual(record['result']['invocation_id'], new_invocation(slot))
            self.assertEqual(record['result']['job'], {'type': 'start', 'mode': 'fail', 'result': 'done'})
            self.assertEqual(self.fixture.receipt()['new_controllers'][str(slot)]['pid'], record['result']['pid'])

    def test_start_without_new_invocation_holds_and_leaves_intent_only(self):
        original = self.fixture.run
        def same(argv):
            result = original(argv)
            if is_start(argv) and argv[4] == 'hound-ci-2.service':
                self.fixture.manager['hound-ci-2.service']['InvocationID'] = original_invocation(2)
            return result
        with patch.object(act, 'run', side_effect=same):
            with self.assertRaisesRegex(RuntimeError, 'NEW distinct systemd invocation'):
                self.fixture.activate()
        starts = self.fixture.receipt()['slot_starts']
        self.assertEqual(starts['1']['stage'], 'started')
        self.assertEqual((starts['2']['stage'], starts['2']['result']), ('start-intent', None))
        self.assertNotIn('3', starts)
        self.assertEqual(sum(is_start(argv) for argv in self.fixture.commands), 2)
        self.assertFalse(any('stop' in argv or 'kill' in argv for argv in self.fixture.commands))

    def test_compatible_job_injected_AFTER_durable_intent_blocks_dispatch(self):
        original = act.Journal.save
        def inject(journal):
            original(journal)
            if journal.value['phase'] == 'start-anchor-intent':
                self.fixture.jobs = [[99, 'hound-ci-image.service', 'reload', 'waiting', '/job', '/unit']]
        with patch.object(act.Journal, 'save', inject):
            with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
                self.fixture.activate()
        self.assertFalse(any(is_start(argv) for argv in self.fixture.commands))
        self.assertIsNone(self.fixture.receipt()['events'][-1]['completion_utc'])
        self.assertEqual(self.fixture.receipt()['events'][-1]['operation'], 'start-anchor')

    def test_later_dispatch_uses_exact_tracked_new_pid_and_still_checks_other_roots(self):
        original = act.Journal.save
        def inject(journal):
            original(journal)
            starts = [event for event in journal.value['events'] if event['operation'] == 'start-anchor']
            if len(starts) == 2 and journal.value['phase'] == 'start-anchor-intent':
                self.fixture.manager['hound-ci-1.service']['MainPID'] = '666'
        with patch.object(act.Journal, 'save', inject):
            with self.assertRaisesRegex(RuntimeError, 'tracked-new state drift'):
                self.fixture.activate()
        self.assertEqual(sum(is_start(argv) for argv in self.fixture.commands), 1)
        self.assertIsNone(self.fixture.receipt()['events'][-1]['completion_utc'])

    def test_explicit_lease_provenance_refusal_no_auto_ack_or_lease_creation(self):
        original = deepcopy(self.fixture.window)
        changes = [('status', 'released'), ('drain_nonce', 'b' * 32),
                   ('manifest_sha256', '0' * 64), ('terminal_certificate_sha256', '0' * 64),
                   ('activation_source', '/nix/store/different.py'), ('activation_sha256', '0' * 64),
                   ('effect_source', '/nix/store/different-effect.py'), ('effect_sha256', '0' * 64),
                   ('effect_structure_sha256', '0' * 64), ('parent_ack', 'invented'),
                   ('manager_identity', {}), ('manager_source_review', {}),
                   ('resources', {}), ('cooperation', []), ('release_rule', 'expires'),
                   ('ignored_not_found', ['unknown.service'])]
        for key, value in changes:
            with self.subTest(key=key):
                self.fixture.window = dict(original, **{key: value})
                self.fixture.write_window()
                with self.assertRaises(RuntimeError):
                    self.fixture.activate()
                self.assertFalse((act.STATE / 'activation.json').exists())
        self.fixture.window = original
        self.fixture.write_window()
        (act.STATE / 'control-window.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.fixture.activate()
        self.assertFalse((act.STATE / 'control-window.json').exists())
        self.assertEqual(self.fixture.commands, [])

    def test_lease_release_after_intent_is_hold_no_operation_or_autoretry(self):
        original = act.Journal.save
        def release(journal):
            original(journal)
            if journal.value['phase'] == 'root-namespace-create-intent':
                self.fixture.window['status'] = 'released'
                self.fixture.write_window()
        with patch.object(act.Journal, 'save', release):
            with self.assertRaisesRegex(RuntimeError, 'lease changed/released'):
                self.fixture.activate()
        self.assertFalse((act.GCROOTS / act.ROOT_NAME).exists())
        self.assertIsNone(self.fixture.receipt()['events'][-1]['completion_utc'])
        self.assertEqual(self.fixture.commands, [])

    def test_nominal_five_minutes_never_auto_expires_or_releases(self):
        self.fixture.window['held_since_utc'] = '2026-01-01T00:00:00+00:00'
        self.fixture.write_window()
        self.fixture.activate()
        self.assertEqual(json.loads((act.STATE / 'control-window.json').read_text())['status'], 'held')



class StructuredTests(unittest.TestCase):
    def test_properties_uses_typed_values_never_textual_execstart(self):
        expected = loaded(1)
        calls = []
        def response(argv):
            calls.append(argv)
            if argv[0] == 'systemctl':
                keys = [item.split('=', 1)[1] for item in argv if item.startswith('--property=')]
                return '\n'.join(key + '=' + expected[key] for key in keys)
            if 'GetUnit' in argv:
                return json.dumps({'type': 'o', 'data': [act.MANAGER_PATH + '/unit/fixture']})
            key = argv[-1]
            signatures = {'FragmentPath': 's', 'DropInPaths': 'as', 'ExecStart': 'a(sasbttttuii)',
                          **{dep: 'as' for dep in ('Requires', 'Wants', 'Requisite', 'BindsTo')}}
            data = shlex.split(expected[key]) if key in ('Requires', 'Wants', 'Requisite', 'BindsTo') else expected[key]
            return json.dumps({'type': signatures[key], 'data': data})
        with patch.object(act, 'run', side_effect=response):
            self.assertEqual(act.properties('hound-ci-1.service'), expected)
        self.assertNotIn('--property=ExecStart', calls[0])
        self.assertTrue(any(argv[-1] == 'ExecStart' and argv[0] == 'busctl' for argv in calls))

    def test_nonservice_dependency_show_does_not_request_service_fields(self):
        requested = []
        def response(argv):
            if argv[0] == 'systemctl':
                requested.extend(item.split('=', 1)[1] for item in argv if item.startswith('--property='))
                return '\n'.join(key + '=' + ('network-online.target' if key == 'Id' else '')
                                 for key in requested)
            if 'GetUnit' in argv:
                return json.dumps({'type': 'o', 'data': [act.MANAGER_PATH + '/unit/fixture']})
            key = argv[-1]
            return json.dumps({'type': 's' if key == 'FragmentPath' else 'as',
                               'data': '/nix/store/fixture.target' if key == 'FragmentPath' else []})
        with patch.object(act, 'run', side_effect=response):
            values = act.properties('network-online.target')
        self.assertFalse(set(act.SERVICE_SCALARS) & set(requested))
        self.assertEqual(values['ExecStart'], [])

    def test_typed_dbus_execstart_array_and_job_schema(self):
        argv = command(1, True)
        payload = {'type': 'a(sasbttttuii)', 'data': [[[argv[0], argv, False, 0, 0, 0, 0, 0, 0, 0]]]}
        with patch.object(act, 'run', return_value=json.dumps(payload)):
            self.assertEqual(act.bus_value([], 'a(sasbttttuii)')[0][1], argv)
        with patch.object(act, 'run', return_value='ExecStart={ path=evil ; argv[]=cache-v2 ; }'):
            with self.assertRaises(json.JSONDecodeError):
                act.bus_value([], 'a(sasbttttuii)')
        with patch.object(act, 'run', return_value=json.dumps({'type': 'a(usssoo)', 'data': [[[1, 'hound-ci-1.service', 'start', 'waiting', '/job', '/unit']]]})):
            with self.assertRaisesRegex(RuntimeError, 'Conflicting queued'):
                act.no_queued_jobs()
        with patch.object(act, 'run', return_value=json.dumps({'type': 'a(usssoo)', 'data': [[[1, 'libk.service', 'start', 'running', '/job', '/unit']]]})):
            act.no_queued_jobs()  # Unrelated work is neither repaired nor held by us.
        with patch.object(act, 'run', return_value=json.dumps({'type': 'a(usssoo)', 'data': [[[1, 'transitive.service', 'stop', 'waiting', '/job', '/unit']]]})):
            with self.assertRaises(RuntimeError):act.no_queued_jobs({'transitive.service'})

    def test_structural_binding_preserves_shared_sysfs_and_typed_set_equivalence(self):
        first = effect_unit('sys-a.device', Requires=['z.target', 'x.target'])
        first['SysFSPath'] = '/sys/devices/a'
        second = deepcopy(first)
        second['Requires'].reverse()
        certificates = {'one.service': {'units': {'sys-a.device': first}},
                        'two.service': {'units': {'sys-a.device': second}}}
        before = act.effect_structure(certificates)
        self.assertEqual(before, act.effect_structure({'one.service': certificates['one.service']}))
        second['SysFSPath'] = '/sys/devices/DIFFERENT'
        with self.assertRaisesRegex(RuntimeError, 'Shared graph equivocation'):
            act.effect_structure(certificates)

    def test_device_peer_set_uses_complete_sysfs_identity_not_Following_only(self):
        ident = 'sys-first.device'
        value = effect_unit(ident)
        value.pop('FollowingSet')
        signatures = {key: 's' for key in effect.UNIT_STRINGS}
        signatures.update({key: 'b' for key in effect.UNIT_BOOLS})
        signatures.update({key: 'as' for key in effect.UNIT_ARRAYS})
        signatures.update(Conditions='a(sbbsi)', Asserts='a(sbbsi)', Job='(uo)', LoadError='(ss)')
        variants = {key: {'type': signatures[key], 'data': val} for key, val in value.items()}
        index = {ident: {'path': '/unit/first', 'following': ''},
                 'sys-second.device': {'path': '/unit/second', 'following': ''}}
        with patch.object(act, 'unit_object', return_value='/unit/first'), patch.object(act, 'bus_value', return_value=variants):
            actual = act.effect_metadata(ident, effect, index,
                    {ident: '/sys/devices/a', 'sys-second.device': '/sys/devices/a'})
            self.assertEqual(actual['FollowingSet'], ['sys-second.device'])
            with self.assertRaisesRegex(RuntimeError, 'SysFSPath peer snapshot'):
                act.effect_metadata(ident, effect, index)
        with patch.object(act, 'bus_value', side_effect=['/sys/devices/a', '/sys/devices/a']):
            self.assertEqual(act.device_sysfs_snapshot(index),
                    {ident: '/sys/devices/a', 'sys-second.device': '/sys/devices/a'})

    def test_noncanonical_source_and_systemd_escaping_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'direct Nix-store'):
            act.direct_source(Path('/tmp/validator.py'), 'a' * 64)
        for value in (b'[Service]\nExecStart=/nix/store/x/bin/hound-ci worker $ARG\n',
                      b'[Service]\nExecStart=/nix/store/x/bin/hound-ci worker\nExecStart=\n',
                      b'[Service]\nExecStart=+/nix/store/x/bin/hound-ci worker\n'):
            with self.assertRaises(RuntimeError):
                act.unit_command(value)


if __name__ == '__main__':
    unittest.main()
