#!/usr/bin/env python3
"""Host-free rollback qualification: real main(), only a temporary filesystem.

No root execution is required. All system/public paths are mapped to fixtures;
OS ownership/EUID and timestamps are mocked. File bytes, permissions, symlinks,
NOFOLLOW/O_EXCL, hashing, fsync and atomic replacement exercise real temp files.
The module's OS facade deliberately exposes no process/service/API operations.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'prepare_rollback_tested', Path(__file__).with_name('prepare-rollback.py'))
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
REVIEWED_IMAGE_SHA = HELPER.OLD_SHA

ARTIFACTS = (
    'rollback-upgrade-intent.json', 'rollback-manifest-v1.json',
    'rollback-manifest-v2.tmp', 'rollback-upgrade-complete.json',
)
INTENT_TIME = '2026-10-05T13:31:00.123456Z'
COMPLETE_TIME = '2026-10-05T13:31:01.654321Z'
ORIGINAL_IMAGE_SHA = '0e856d33b2e9c08e54863b3916d7a3aef7fce69d513f7f163450e3a015d9056b'


class Metadata:
    """Keep real inode identity/modes; supply the operator's reviewed ownership."""
    def __init__(self, underlying, **overrides):
        self.underlying = underlying
        self.overrides = {'st_uid': 0, 'st_gid': 0, **overrides}

    def __getattr__(self, name):
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.underlying, name)


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='prepare-rollback-test-')
        self.root = Path(self.temp.name)
        self.base = self.root / 'backup'
        self.roots = self.root / 'gcroots'
        self.enable = self.root / 'multi-user.target.wants'
        self.image = self.root / 'base.qcow2'
        self.public = self.root / 'public'
        for directory in (self.base, self.roots, self.enable, self.public):
            directory.mkdir(mode=0o700)
        self.manifest = self.base / 'rollback-manifest.json'
        self.image_bytes = b'fixture-only original immutable qcow2 bytes\n'
        self.image_sha = hashlib.sha256(self.image_bytes).hexdigest()
        self.write(self.image, self.image_bytes, 0o444)
        self.physical(HELPER.PROFILE).mkdir(parents=True)
        self.link('/run/current-system', HELPER.PROFILE)
        self.old = {'host_profile': HELPER.PROFILE, 'old_unit_links': [],
                    str(self.roots): [], str(self.enable): []}
        self.units = {}
        for slot, prefix in HELPER.OLD.items():
            name = f'hound-ci-{slot}.service'
            target = f'/nix/store/{prefix}-unit-{name}/{name}'
            attached = f'/etc/systemd/system.attached/{name}'
            data = f'[Unit]\nDescription=original fixture slot {slot}\n'.encode()
            self.write(self.physical(target), data, 0o444)
            self.write(self.base / name, data, 0o600)
            self.link(attached, target)
            self.old['old_unit_links'].append(
                {'unit': name, 'path': attached, 'target': target})
            self.units[name] = (attached, target, data)
        self.expected = {}
        for directory in (self.roots, self.enable):
            expected = {f'hound-ci-{slot}.service': prefix
                        for slot, prefix in HELPER.OLD.items()}
            expected.update(HELPER.AUX if directory == self.roots else {
                'hound-ci-firewall.service': HELPER.AUX['hound-ci-firewall.service']})
            self.expected[directory] = {}
            for name, prefix in expected.items():
                target = f'/nix/store/{prefix}-unit-{name}'
                if directory == self.enable:
                    target += f'/{name}'
                    if not self.physical(target).exists():
                        self.write(self.physical(target), b'[Unit]\nAuxiliary fixture\n', 0o444)
                else:
                    self.physical(target).mkdir(parents=True, exist_ok=True)
                self.link(directory / name, target)
                self.expected[directory][name] = target
                if name in self.units:
                    self.old[str(directory)].append(
                        {'path': str(directory / name), 'target': target})
        # Deliberately noncanonical JSON: the retained v1 MUST be byte-for-byte.
        self.raw = (json.dumps(self.old, indent=3) + ' \n\n').encode()
        self.write(self.manifest, self.raw, 0o600)
        self.events = []
        self.opens = []
        self.fds = {}
        self.fstat_counts = {}
        self.overrides = {}
        self.euid = 0
        self.before_create = None
        self.fsync_hook = None
        self.fstat_hook = None
        self.replace_error = None
        self.readlink_hook = None
        fixture = self

        class FixturePath(type(Path())):
            def lstat(self):
                original = str(self)
                return Metadata(fixture.physical(self).lstat(),
                                **fixture.overrides.get(original, {}))

            def iterdir(self):
                for entry in fixture.physical(self).iterdir():
                    yield self / entry.name

            def exists(self):
                return fixture.physical(self).exists()

            def resolve(self, strict=False):
                # /run/current-system is the only resolve in the reviewed main.
                if str(self) != '/run/current-system':
                    raise AssertionError(f'Unexpected resolve: {self}')
                if strict and not fixture.physical(HELPER.PROFILE).is_dir():
                    raise FileNotFoundError(HELPER.PROFILE)
                return FixturePath(HELPER.PROFILE)

            def replace(self, destination):
                fixture.events.append(('replace', str(self), str(destination)))
                if fixture.replace_error:
                    raise fixture.replace_error
                fixture.physical(self).replace(fixture.physical(destination))
                return FixturePath(destination)

        self.Path = FixturePath
        self.os = types.SimpleNamespace(
            O_RDONLY=os.O_RDONLY, O_WRONLY=os.O_WRONLY, O_CLOEXEC=os.O_CLOEXEC,
            O_NOFOLLOW=os.O_NOFOLLOW, O_DIRECTORY=os.O_DIRECTORY,
            O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL,
            geteuid=lambda: fixture.euid, open=self.open, fstat=self.fstat,
            read=os.read, close=self.close, readlink=self.readlink,
            fdopen=os.fdopen, fsync=self.fsync)
        self.stack = contextlib.ExitStack()
        for name, value in (
            ('Path', self.Path), ('os', self.os), ('BASE', self.Path(self.base)),
            ('ROOTS', self.Path(self.roots)), ('ENABLE', self.Path(self.enable)),
            ('OLD_IMAGE', self.Path(self.image)), ('OLD_SHA', self.image_sha),
        ):
            self.stack.enter_context(mock.patch.object(HELPER, name, value))
        self.clock = self.stack.enter_context(mock.patch.object(
            HELPER, 'utc_timestamp', side_effect=[INTENT_TIME, COMPLETE_TIME]))
        self.read_spy = self.stack.enter_context(mock.patch.object(
            HELPER, 'read', wraps=HELPER.read))

    def physical(self, path):
        """Fail closed rather than accidentally reading any actual public path."""
        path = Path(path)
        if path.is_relative_to(self.root):
            return path
        if str(path) == '/run/current-system' or path.is_relative_to('/nix/store') or path.is_relative_to('/etc/systemd/system.attached'):
            return self.public / str(path).lstrip('/')
        raise AssertionError(f'Forbidden nonfixture host path: {path}')

    def write(self, path, data, mode=0o600):
        path = self.physical(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def link(self, path, target):
        path = self.physical(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def relink(self, path, target):
        self.physical(path).unlink()
        self.link(path, target)

    def save_old(self):
        self.raw = (json.dumps(self.old, indent=3) + ' \n\n').encode()
        self.write(self.manifest, self.raw)

    def open(self, path, flags, mode=0o777):
        original = str(path)
        physical = self.physical(path)
        self.opens.append((original, flags, mode))
        if flags & os.O_CREAT:
            self.events.append(('create', original))
            if self.before_create:
                self.before_create(original)
        fd = os.open(physical, flags, mode)
        self.fds[fd] = original
        return fd

    def fstat(self, fd):
        original = self.fds[fd]
        count = self.fstat_counts.get(original, 0) + 1
        self.fstat_counts[original] = count
        underlying = os.fstat(fd)
        overrides = dict(self.overrides.get(original, {}))
        if self.fstat_hook:
            overrides.update(self.fstat_hook(original, count, underlying) or {})
        return Metadata(underlying, **overrides)

    def close(self, fd):
        os.close(fd)
        self.fds.pop(fd, None)

    def readlink(self, path):
        original = str(path)
        if self.readlink_hook:
            replacement = self.readlink_hook(original)
            if replacement is not None:
                return replacement
        return os.readlink(self.physical(path))

    def fsync(self, fd):
        original = self.fds[fd]
        kind = 'fsync-dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'fsync-file'
        self.events.append((kind, original))
        if self.fsync_hook:
            self.fsync_hook(kind, original)
        os.fsync(fd)

    def run(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            HELPER.main()
        return output.getvalue()

    def snapshot(self):
        values = {}
        for path in self.base.iterdir():
            meta = path.lstat()
            data = os.readlink(path) if path.is_symlink() else path.read_bytes()
            values[path.name] = (stat.S_IMODE(meta.st_mode), data)
        return values

    def close_fixture(self):
        self.stack.close()
        self.temp.cleanup()


class PrepareRollbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close_fixture)

    def refuse(self, exception=(RuntimeError, OSError, ValueError, KeyError, TypeError)):
        fixture = self.fixture
        before = fixture.snapshot()
        with self.assertRaises(exception):
            fixture.run()
        self.assertEqual(fixture.snapshot(), before)
        self.assertEqual(fixture.events, [], 'Qualification refusal must have NO mutations')
        self.fixture.clock.assert_not_called()

    def test_original_image_constant_is_the_reviewed_public_pin(self):
        # Fixture hashing is real; only the fixture's expected pin is substituted.
        self.assertEqual(REVIEWED_IMAGE_SHA, ORIGINAL_IMAGE_SHA)
        with mock.patch.object(HELPER, 'OLD_SHA', ORIGINAL_IMAGE_SHA):
            self.refuse()

    def test_actual_main_happy_path_full_inventory_and_exact_original_bytes(self):
        f = self.fixture
        original_links = {str(directory / name): os.readlink(directory / name)
                          for directory, names in f.expected.items() for name in names}
        output = f.run()
        raw = f.manifest.read_bytes()
        new = json.loads(raw)
        self.assertEqual(new['schema'], 2)
        self.assertEqual(new['host_profile'], HELPER.PROFILE)
        self.assertEqual(new['host_profile_resolved'], HELPER.PROFILE)
        self.assertEqual(new['old_image'], {'path': str(f.image), 'sha256': f.image_sha})
        self.assertEqual(len(new['old_unit_links']), 4)
        for entry in new['old_unit_links']:
            attached, target, data = f.units[entry['unit']]
            self.assertEqual(entry, {
                'unit': entry['unit'], 'path': attached, 'target': target,
                'uid': 0, 'gid': 0, 'sha256': hashlib.sha256(data).hexdigest(),
                'backup': str(f.base / entry['unit']),
            })
        for directory, count in ((f.roots, 8), (f.enable, 5)):
            self.assertEqual(len(f.old[str(directory)]), 4)
            self.assertEqual(len(new[str(directory)]), count)
            self.assertEqual(new[str(directory)], [
                {'path': str(directory / name), 'target': target, 'uid': 0, 'gid': 0}
                for name, target in sorted(f.expected[directory].items())])
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        intent = json.loads((f.base / ARTIFACTS[0]).read_bytes())
        self.assertEqual(intent, {
            'phase': 'rollback-schema-upgrade-intent', 'timestamp_utc': INTENT_TIME,
            'old_manifest_sha256': hashlib.sha256(f.raw).hexdigest(),
            'new_manifest_sha256': hashlib.sha256(raw).hexdigest(),
        })
        complete = json.loads((f.base / ARTIFACTS[3]).read_bytes())
        self.assertEqual(complete, {'phase': 'rollback-schema-2-complete',
                                    'timestamp_utc': COMPLETE_TIME})
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assertEqual(f.image.read_bytes(), f.image_bytes)
        for path, target in original_links.items():
            self.assertEqual(os.readlink(path), target)
        for path in (f.manifest, *(f.base / name for name in (ARTIFACTS[0], ARTIFACTS[1], ARTIFACTS[3]))):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn('ROLLBACK_SCHEMA_2_COMPLETE', output)
        self.assertEqual(f.clock.call_count, 2)

    def test_path_shadow_regression_final_read_is_manifest_not_inventory_symlink(self):
        f = self.fixture
        f.run()
        calls = [str(call.args[0]) for call in f.read_spy.call_args_list]
        self.assertEqual(calls[0], str(f.manifest))
        self.assertEqual(calls[-1], str(f.manifest))
        self.assertEqual(calls.count(str(f.manifest)), 2)
        self.assertNotIn(str(f.enable / 'hound-ci-firewall.service'), calls)

    def test_path_shadow_regression_atomic_replace_targets_manifest_only(self):
        f = self.fixture
        f.run()
        self.assertEqual([event for event in f.events if event[0] == 'replace'], [
            ('replace', str(f.base / ARTIFACTS[2]), str(f.manifest))])
        self.assertTrue((f.enable / 'hound-ci-firewall.service').is_symlink())
        self.assertEqual(os.readlink(f.enable / 'hound-ci-firewall.service'),
                         f.expected[f.enable]['hound-ci-firewall.service'])

    def test_intent_is_first_mutation_and_durable_before_original_backup(self):
        f = self.fixture
        f.run()
        creates = [('create', str(f.base / name)) for name in ARTIFACTS]
        self.assertEqual([event for event in f.events if event[0] == 'create'], creates)
        self.assertEqual(f.events[:4], [
            creates[0], ('fsync-file', str(f.base / ARTIFACTS[0])),
            ('fsync-dir', str(f.base)), creates[1]])
        replace_index = f.events.index(('replace', str(f.base / ARTIFACTS[2]), str(f.manifest)))
        self.assertEqual(f.events[replace_index + 1], ('fsync-dir', str(f.base)))
        self.assertGreater(f.events.index(creates[3]), replace_index + 1)
        for path, flags, mode in f.opens:
            if flags & os.O_CREAT:
                self.assertEqual(flags & (os.O_EXCL | os.O_NOFOLLOW), os.O_EXCL | os.O_NOFOLLOW)
                self.assertEqual(mode, 0o600)
            elif not flags & os.O_DIRECTORY:
                self.assertTrue(flags & os.O_NOFOLLOW, path)

    def test_nonroot_operator_refuses_without_writes(self):
        self.fixture.euid = 1000
        self.refuse()

    def test_backup_directory_owner_group_or_mode_refuses(self):
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFDIR | 0o755},
                         {'st_mode': stat.S_IFREG | 0o700}):
            with self.subTest(override=override):
                self.fixture.overrides[str(self.fixture.base)] = override
                self.refuse()

    def test_manifest_metadata_requires_root_root_0600_regular_bounded_file(self):
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFREG | 0o644},
                         {'st_mode': stat.S_IFDIR | 0o600},
                         {'st_size': 0}, {'st_size': 65537}):
            with self.subTest(override=override):
                self.fixture.overrides[str(self.fixture.manifest)] = override
                self.refuse()

    def test_manifest_symlink_is_never_followed(self):
        f = self.fixture
        retained = f.base / 'fixture-original.json'
        f.manifest.rename(retained)
        f.manifest.symlink_to(retained)
        self.refuse(OSError)

    def test_schema_extra_missing_or_already_upgraded_refuses(self):
        f = self.fixture
        original = dict(f.old)
        for changed in ({**original, 'schema': 2},
                        {key: value for key, value in original.items() if key != 'host_profile'},
                        {**original, 'unexpected': []}):
            with self.subTest(keys=sorted(changed)):
                f.old = changed
                f.save_old()
                self.refuse()

    def test_invalid_json_refuses(self):
        self.fixture.write(self.fixture.manifest, b'{broken JSON\n')
        self.refuse()

    def test_baseline_profile_or_manifest_profile_drift_refuses(self):
        f = self.fixture
        f.readlink_hook = lambda path: '/nix/store/unexpected-profile' if path == '/run/current-system' else None
        self.refuse()
        f.readlink_hook = None
        f.old['host_profile'] = '/nix/store/unexpected-profile'
        f.save_old()
        self.refuse()

    def test_old_unit_inventory_requires_exactly_four_unique_reviewed_entries(self):
        f = self.fixture
        original = list(f.old['old_unit_links'])
        variants = [original[:-1], original + [original[0]],
                    [original[0], original[0], original[2], original[3]],
                    [{**original[0], 'extra': True}, *original[1:]],
                    [{**original[0], 'target': '/nix/store/unexpected'}, *original[1:]],
                    [{**original[0], 'path': '/etc/systemd/system/other.service'}, *original[1:]]]
        for entries in variants:
            with self.subTest(entries=entries):
                f.old['old_unit_links'] = entries
                f.save_old()
                self.refuse()

    def test_attached_unit_target_drift_refuses(self):
        f = self.fixture
        attached, _, _ = f.units['hound-ci-1.service']
        f.relink(attached, '/nix/store/unexpected-unit/hound-ci-1.service')
        self.refuse()

    def test_attached_unit_symlink_owner_group_and_type_are_qualified(self):
        f = self.fixture
        attached, _, _ = f.units['hound-ci-1.service']
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFREG | 0o777}):
            with self.subTest(override=override):
                f.overrides[attached] = override
                self.refuse()

    def test_original_immutable_unit_metadata_requires_root_root_0444(self):
        f = self.fixture
        _, target, _ = f.units['hound-ci-1.service']
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFREG | 0o600}):
            with self.subTest(override=override):
                f.overrides[target] = override
                self.refuse()

    def test_retained_unit_copy_metadata_requires_root_root_0600(self):
        f = self.fixture
        path = str(f.base / 'hound-ci-1.service')
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFREG | 0o444}):
            with self.subTest(override=override):
                f.overrides[path] = override
                self.refuse()

    def test_retained_unit_copy_bytes_hash_mismatch_refuses(self):
        f = self.fixture
        f.write(f.base / 'hound-ci-1.service', b'[Unit]\nDifferent bytes\n')
        self.refuse()

    def test_retained_unit_copy_symlink_is_never_followed(self):
        f = self.fixture
        copy = f.base / 'hound-ci-1.service'
        original = f.base / 'fixture-unit-copy'
        copy.rename(original)
        copy.symlink_to(original)
        self.refuse(OSError)

    def test_original_controller_root_and_enable_inventory_must_be_complete(self):
        f = self.fixture
        for directory in (f.roots, f.enable):
            original = list(f.old[str(directory)])
            for entries in (original[:-1],
                            [original[0], original[0], original[2], original[3]]):
                with self.subTest(directory=directory, entries=entries):
                    f.old[str(directory)] = entries
                    f.save_old()
                    self.refuse()
            f.old[str(directory)] = original
            f.save_old()

    def test_original_inventory_extra_keys_wrong_parent_or_target_refuses(self):
        f = self.fixture
        for directory in (f.roots, f.enable):
            original = list(f.old[str(directory)])
            for changed in ({**original[0], 'extra': True},
                            {**original[0], 'path': str(f.base / 'hound-ci-1.service')},
                            {**original[0], 'target': '/nix/store/unexpected-target'}):
                with self.subTest(directory=directory, changed=changed):
                    f.old[str(directory)] = [changed, *original[1:]]
                    f.save_old()
                    self.refuse()
            f.old[str(directory)] = original
            f.save_old()

    def test_unexpected_full_root_inventory_refuses(self):
        f = self.fixture
        f.link(f.roots / 'unexpected.service', '/nix/store/unexpected')
        self.refuse()

    def test_unexpected_full_enable_inventory_refuses(self):
        f = self.fixture
        f.link(f.enable / 'unexpected.service', '/nix/store/unexpected')
        self.refuse()

    def test_missing_auxiliary_root_refuses(self):
        f = self.fixture
        (f.roots / 'hound-ci.slice').unlink()
        self.refuse()

    def test_missing_firewall_enable_link_refuses(self):
        f = self.fixture
        (f.enable / 'hound-ci-firewall.service').unlink()
        self.refuse()

    def test_full_root_target_drift_refuses(self):
        f = self.fixture
        f.relink(f.roots / 'hound-ci.slice', '/nix/store/unexpected-slice')
        self.refuse()

    def test_full_enable_firewall_target_drift_refuses(self):
        f = self.fixture
        f.relink(f.enable / 'hound-ci-firewall.service', '/nix/store/unexpected-firewall')
        self.refuse()

    def test_full_root_and_enable_ownership_refuses(self):
        f = self.fixture
        for directory in (f.roots, f.enable):
            for override in ({'st_uid': 1000}, {'st_gid': 1000}):
                with self.subTest(directory=directory, override=override):
                    path = str(directory / 'hound-ci-firewall.service')
                    f.overrides[path] = override
                    self.refuse()
            del f.overrides[path]

    def test_missing_original_immutable_auxiliary_target_refuses(self):
        f = self.fixture
        target = f.expected[f.roots]['hound-ci.slice']
        f.physical(target).rmdir()
        self.refuse()

    def test_old_image_actual_hash_mismatch_refuses(self):
        f = self.fixture
        f.image.chmod(0o600)  # Fixture author, not the helper, injects byte drift.
        f.write(f.image, b'drifted image bytes\n', 0o444)
        self.refuse()

    def test_old_image_metadata_requires_root_root_0444_regular_file(self):
        f = self.fixture
        for override in ({'st_uid': 1000}, {'st_gid': 1000},
                         {'st_mode': stat.S_IFREG | 0o600},
                         {'st_mode': stat.S_IFDIR | 0o444}):
            with self.subTest(override=override):
                f.overrides[str(f.image)] = override
                self.refuse()

    def test_old_image_symlink_is_never_followed(self):
        f = self.fixture
        original = f.root / 'fixture-original.qcow2'
        f.image.rename(original)
        f.image.symlink_to(original)
        self.refuse(OSError)

    def test_old_image_identity_drift_during_hash_refuses(self):
        f = self.fixture
        for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
            with self.subTest(field=field):
                f.fstat_counts.clear()
                f.fstat_hook = lambda path, count, meta: {
                    field: getattr(meta, field) + 1
                } if path == str(f.image) and count == 2 else None
                self.refuse()

    def test_manifest_byte_drift_final_check_refuses_before_any_intent(self):
        f = self.fixture
        actual_read = f.read_spy._mock_wraps
        count = 0

        def drift(path, *args, **kwargs):
            nonlocal count
            if str(path) == str(f.manifest):
                count += 1
                if count == 2:
                    f.write(f.manifest, f.raw + b' \n')
            return actual_read(path, *args, **kwargs)

        f.read_spy.side_effect = drift
        with self.assertRaisesRegex(RuntimeError, 'changed during qualification'):
            f.run()
        self.assertEqual(count, 2)
        self.assertEqual(f.events, [])
        self.assertEqual(f.manifest.read_bytes(), f.raw + b' \n')
        self.assertTrue(all(not (f.base / name).exists() for name in ARTIFACTS))
        f.clock.assert_not_called()

    def test_profile_drift_final_check_refuses_before_any_intent(self):
        f = self.fixture
        count = 0

        def drift(path):
            nonlocal count
            if path == '/run/current-system':
                count += 1
                return HELPER.PROFILE if count == 1 else '/nix/store/drifted-profile'
            return None

        f.readlink_hook = drift
        self.refuse()
        self.assertEqual(count, 2)

    def test_each_preexisting_upgrade_artifact_refuses_before_new_writes(self):
        f = self.fixture
        for name in ARTIFACTS:
            with self.subTest(artifact=name):
                path = f.base / name
                f.write(path, b'PREEXISTING RECEIPT MUST STAY EXACT\n')
                self.refuse()
                path.unlink()

    def test_each_dangling_upgrade_artifact_symlink_refuses(self):
        f = self.fixture
        for name in ARTIFACTS:
            with self.subTest(artifact=name):
                path = f.base / name
                path.symlink_to(f.base / 'nonexistent-target')
                self.refuse()
                path.unlink()

    def test_exclusive_intent_collision_cannot_overwrite_or_advance(self):
        f = self.fixture
        path = f.base / ARTIFACTS[0]
        f.before_create = lambda original: f.write(path, b'CONCURRENT OWNER\n') if original == str(path) else None
        with self.assertRaises(FileExistsError):
            f.run()
        self.assertEqual(path.read_bytes(), b'CONCURRENT OWNER\n')
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertEqual(f.events, [('create', str(path))])
        self.assertTrue(all(not (f.base / name).exists() for name in ARTIFACTS[1:]))

    def test_exclusive_v1_collision_keeps_intent_and_original_no_auto_rollback(self):
        f = self.fixture
        path = f.base / ARTIFACTS[1]
        f.before_create = lambda original: f.write(path, b'CONCURRENT V1\n') if original == str(path) else None
        with self.assertRaises(FileExistsError):
            f.run()
        self.assertEqual(path.read_bytes(), b'CONCURRENT V1\n')
        self.assertTrue((f.base / ARTIFACTS[0]).exists())
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_exclusive_temp_collision_keeps_exact_v1_and_original(self):
        f = self.fixture
        path = f.base / ARTIFACTS[2]
        f.before_create = lambda original: f.write(path, b'CONCURRENT TMP\n') if original == str(path) else None
        with self.assertRaises(FileExistsError):
            f.run()
        self.assertEqual(path.read_bytes(), b'CONCURRENT TMP\n')
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def assert_no_automatic_recovery_on_retry(self):
        f = self.fixture
        before = f.snapshot()
        events = list(f.events)
        with self.assertRaises(RuntimeError):
            f.run()
        self.assertEqual(f.snapshot(), before)
        self.assertEqual(f.events, events)

    def test_interrupted_intent_fsync_leaves_receipt_and_never_advances(self):
        f = self.fixture

        def fail(kind, path):
            if kind == 'fsync-file' and path == str(f.base / ARTIFACTS[0]):
                raise OSError('injected intent fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertTrue((f.base / ARTIFACTS[0]).exists())
        self.assertTrue(all(not (f.base / name).exists() for name in ARTIFACTS[1:]))
        self.assert_no_automatic_recovery_on_retry()

    def test_interrupted_intent_directory_fsync_never_creates_v1(self):
        f = self.fixture

        def fail(kind, path):
            if kind == 'fsync-dir':
                raise OSError('injected intent directory fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertTrue(all(not (f.base / name).exists() for name in ARTIFACTS[1:]))
        self.assert_no_automatic_recovery_on_retry()

    def test_interrupted_original_backup_fsync_never_creates_temp(self):
        f = self.fixture

        def fail(kind, path):
            if kind == 'fsync-file' and path == str(f.base / ARTIFACTS[1]):
                raise OSError('injected v1 fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_interrupted_temp_fsync_preserves_original_and_no_complete(self):
        f = self.fixture

        def fail(kind, path):
            if kind == 'fsync-file' and path == str(f.base / ARTIFACTS[2]):
                raise OSError('injected temp fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertTrue((f.base / ARTIFACTS[2]).exists())
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_atomic_replace_failure_retains_intent_v1_temp_no_auto_retry(self):
        f = self.fixture
        f.replace_error = OSError('injected atomic replace failure')
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual(f.manifest.read_bytes(), f.raw)
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertTrue((f.base / ARTIFACTS[2]).exists())
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_postreplace_directory_fsync_failure_never_undoes_upgrade(self):
        f = self.fixture
        count = 0

        def fail(kind, path):
            nonlocal count
            if kind == 'fsync-dir':
                count += 1
                if count == 4:
                    raise OSError('injected postreplace directory fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual(json.loads(f.manifest.read_bytes())['schema'], 2)
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assertFalse((f.base / ARTIFACTS[3]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_exclusive_complete_collision_never_overwrites_or_rolls_back(self):
        f = self.fixture
        path = f.base / ARTIFACTS[3]
        f.before_create = lambda original: f.write(path, b'CONCURRENT COMPLETE\n') if original == str(path) else None
        with self.assertRaises(FileExistsError):
            f.run()
        self.assertEqual(path.read_bytes(), b'CONCURRENT COMPLETE\n')
        self.assertEqual(json.loads(f.manifest.read_bytes())['schema'], 2)
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_complete_fsync_failure_preserves_upgrade_and_never_auto_recovers(self):
        f = self.fixture

        def fail(kind, path):
            if kind == 'fsync-file' and path == str(f.base / ARTIFACTS[3]):
                raise OSError('injected completion fsync failure')

        f.fsync_hook = fail
        with self.assertRaises(OSError):
            f.run()
        self.assertEqual(json.loads(f.manifest.read_bytes())['schema'], 2)
        self.assertEqual((f.base / ARTIFACTS[1]).read_bytes(), f.raw)
        self.assertFalse((f.base / ARTIFACTS[2]).exists())
        self.assert_no_automatic_recovery_on_retry()

    def test_successful_upgrade_cannot_be_run_twice(self):
        self.fixture.run()
        self.assert_no_automatic_recovery_on_retry()

    def test_fixture_refuses_unmapped_public_paths_without_reading_host(self):
        f = self.fixture
        for path in ('/etc/shadow', '/proc/1/status', '/var/lib/hound-ci/real-state'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(AssertionError, 'Forbidden nonfixture host path'):
                    f.os.open(path, os.O_RDONLY)
        self.assertEqual(f.events, [])


class TimestampTests(unittest.TestCase):
    def test_timestamp_is_timezone_aware_microsecond_utc_z(self):
        fixed = datetime(2026, 10, 5, 13, 31, 0, 123456, tzinfo=timezone.utc)
        with mock.patch.object(HELPER, 'datetime') as clock:
            clock.now.return_value = fixed
            self.assertEqual(HELPER.utc_timestamp(), INTENT_TIME)
            clock.now.assert_called_once_with(timezone.utc)


class ExecutionReceiptTests(unittest.TestCase):
    def test_operational_set_euo_prefix_preserves_helper_failure_before_success_date(self):
        # Regression for the actual13:28 wrapper: a final date previously hid
        # helper exit1. Execute the operational prefix with a synthetic helper,
        # never a root/program/API operation; no success receipt may follow it.
        import subprocess
        command = "set -euo pipefail\nhelper() { return 37; }\nhelper\nprintf 'SUCCESS_DATE_WOULD_MASK_RC'\n"
        result = subprocess.run(['bash', '-c', command], capture_output=True, check=False)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
