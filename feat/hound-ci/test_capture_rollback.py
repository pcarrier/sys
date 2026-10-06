#!/usr/bin/env python3
"""Host-free capture-rollback tests: temp directories stand in for the store,
the attached units, the GC roots and the image; only root ownership is modelled.
The cross-tests feed the captured ledger to activate-cache-v2.py's Backup."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = load('capture', 'capture-rollback.py')
act = load('activate', 'activate-cache-v2.py')
LEGACY = ('hound-ci-1.service', 'hound-ci-2.service', 'hound-ci-3.service', 'hound-ci-4.service',
          'hound-ci-firewall.service', 'hound-ci-image.service', 'hound-ci.slice', 'hound-ci-storage.service')


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.store = self.root / 'store'
        self.store.mkdir()
        attached = self.root / 'attached'
        enable = attached / 'multi-user.target.wants'
        gcroots = self.root / 'gcroots' / 'hound-ci'
        enable.mkdir(parents=True)
        gcroots.mkdir(parents=True)
        (self.root / 'var').mkdir()
        profile = self.store / 'profile-nixos-system-hound'
        profile.mkdir()
        current = self.root / 'current-system'
        current.symlink_to(profile)
        image = self.root / 'var' / 'base-cache-v2.qcow2'
        image.write_bytes(b'cache-v2 image')
        image.chmod(0o444)
        self.old = {}
        for slot in (1, 2, 3, 4):
            name = f'hound-ci-{slot}.service'
            unit = self.store / f'cache-v2-unit-{name}'
            unit.mkdir()
            (unit / name).write_bytes(f'[Service]\nExecStart=old {slot}\n'.encode())
            (unit / name).chmod(0o444)
            self.old[slot] = str(unit)
            (attached / name).symlink_to(unit / name)
        for name in LEGACY:
            legacy = self.store / f'legacy-unit-{name}'
            legacy.mkdir()
            (legacy / name).write_text(name)
            (gcroots / name).symlink_to(legacy)
        for name in LEGACY[:4] + ('hound-ci-firewall.service',):
            (enable / name).symlink_to(self.store / f'legacy-unit-{name}' / name)
        retained = gcroots / cap.RETAINED
        retained.mkdir()
        for slot, unit in self.old.items():
            (retained / f'hound-ci-{slot}.service').symlink_to(unit)
        self.paths = SimpleNamespace(attached=attached, enable=enable, gcroots=gcroots, current=current,
                                     profile=profile, image=image, retained=retained,
                                     backup=self.root / 'var' / 'rollout-main-slot-backup')
        self.stack = ExitStack()
        for module, name, value in (
                (cap, 'STORE', str(self.store)), (cap, 'ATTACHED', attached), (cap, 'ENABLE', enable),
                (cap, 'GCROOTS', gcroots), (cap, 'CURRENT', current), (cap, 'PROFILE', str(profile)),
                (cap, 'IMAGE', image), (cap, 'IMAGE_SHA', hashlib.sha256(b'cache-v2 image').hexdigest()),
                (cap, 'OLD', self.old), (cap, 'BACKUP', self.paths.backup),
                (cap, 'PINNED_PYTHON', sys.executable),
                (act, 'ATTACHED', attached), (act, 'ENABLE', enable), (act, 'GCROOTS', gcroots),
                (act, 'CURRENT', current), (act, 'BACKUP', self.paths.backup), (act, 'OLD_IMAGE', image),
                (act, 'OLD_SHA', hashlib.sha256(b'cache-v2 image').hexdigest())):
            self.stack.enter_context(patch.object(module, name, value))
        # Fixtures belong to the test's user: model root ownership only,
        # keeping real modes, types, descriptors and no-follow opens.
        original_fstat, original_lstat = os.fstat, Path.lstat
        self.owner = {'uid': 0, 'gid': 0}
        self.foreign = set()  # paths whose lstat reports another owner
        def owned(meta, path=None):
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_nlink')
            uid = 1000 if path is not None and Path(path) in self.foreign else self.owner['uid']
            return SimpleNamespace(**{field: getattr(meta, field) for field in fields},
                                   st_uid=uid, st_gid=self.owner['gid'])
        self.stack.enter_context(patch.object(os, 'fstat', side_effect=lambda fd: owned(original_fstat(fd))))
        self.stack.enter_context(patch.object(Path, 'lstat', autospec=True,
                                              side_effect=lambda path: owned(original_lstat(path), path)))
        self.euid = self.stack.enter_context(patch.object(cap.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(act, 'store_file', side_effect=self.store_file))

    def store_file(self, path):
        path = Path(path)
        assert path.is_relative_to(self.store) and path.resolve(strict=True) == path
        return act.read_file(path, immutable=True)

    def capture(self):
        return cap.capture()

    def manifest(self):
        return json.loads((self.paths.backup / 'rollback-manifest.json').read_text())

    def close(self):
        self.stack.close()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.addCleanup(self.fixture.close)

    def assertRefusedWithoutLedger(self, pattern):
        with self.assertRaisesRegex(RuntimeError, 'CAPTURE_REFUSED .*' + pattern):
            self.fixture.capture()
        self.assertFalse(os.path.lexists(self.fixture.paths.backup))

    def test_capture_writes_the_complete_write_once_ledger(self):
        returned = self.fixture.capture()
        backup = self.fixture.paths.backup
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in backup.iterdir()),
                         sorted(['rollback-manifest.json'] + [f'hound-ci-{s}.service' for s in (1, 2, 3, 4)]))
        for entry in backup.iterdir():
            self.assertEqual(stat.S_IMODE(os.stat(entry).st_mode), 0o600)
        manifest = self.fixture.manifest()
        self.assertEqual(manifest, returned)
        self.assertEqual((manifest['schema'], manifest['generation']), (2, 'main-slot-20261006'))
        self.assertEqual(manifest['host_profile'], str(self.fixture.paths.profile))
        for entry in manifest['old_unit_links']:
            data = (backup / entry['unit']).read_bytes()
            self.assertEqual(data, Path(entry['target']).read_bytes())
            self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry['target'], f"{self.fixture.old[int(entry['unit'][9])]}/{entry['unit']}")
        roots = manifest[str(self.fixture.paths.gcroots)]
        self.assertEqual(sorted(Path(e['path']).name for e in roots), sorted(LEGACY))
        self.assertEqual(len(manifest[str(self.fixture.paths.enable)]), 5)
        self.assertEqual(sorted(e['target'] for e in manifest['retained_root_directories'][cap.RETAINED]),
                         sorted(self.fixture.old.values()))
        with self.assertRaisesRegex(RuntimeError, 'never recapture'):
            self.fixture.capture()

    def test_existing_ledger_directory_is_never_reused(self):
        self.fixture.paths.backup.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'never recapture'):
            self.fixture.capture()
        self.assertEqual(list(self.fixture.paths.backup.iterdir()), [])

    def test_identity_and_interpreter_are_required(self):
        self.fixture.euid.return_value = 1000
        self.assertRefusedWithoutLedger('Root operator')
        self.fixture.euid.return_value = 0
        with patch.object(cap, 'PINNED_PYTHON', '/nix/store/other-python3'):
            self.assertRefusedWithoutLedger('pinned Nix Python')

    def test_profile_drift_refuses(self):
        other = self.fixture.store / 'other-profile'
        other.mkdir()
        self.fixture.paths.current.unlink()
        self.fixture.paths.current.symlink_to(other)
        self.assertRefusedWithoutLedger('Host profile')

    def test_attached_unit_drift_refuses(self):
        link = self.fixture.paths.attached / 'hound-ci-3.service'
        link.unlink()
        link.symlink_to(self.fixture.store / 'legacy-unit-hound-ci-3.service' / 'hound-ci-3.service')
        self.assertRefusedWithoutLedger('not linked to the loaded cache-v2 unit')

    def test_writable_or_non_store_unit_refuses(self):
        unit = Path(self.fixture.old[2]) / 'hound-ci-2.service'
        unit.chmod(0o644)
        self.assertRefusedWithoutLedger('immutable bounded store file')
        unit.chmod(0o444)
        outside = self.fixture.root / 'hound-ci-2.service'
        outside.write_text('x')
        link = self.fixture.paths.attached / 'hound-ci-2.service'
        link.unlink()
        link.symlink_to(outside)
        self.assertRefusedWithoutLedger('not in the store')

    def test_retained_namespace_missing_or_drifted_refuses(self):
        retained = self.fixture.paths.retained
        extra = retained / 'hound-ci-5.service'
        extra.symlink_to(self.fixture.store / 'legacy-unit-hound-ci.slice')
        self.assertRefusedWithoutLedger('Retained cache-v2 GC roots differ')
        extra.unlink()
        moved = retained / 'hound-ci-4.service'
        moved.unlink()
        moved.symlink_to(self.fixture.old[3])
        self.assertRefusedWithoutLedger('Retained cache-v2 GC roots differ')

    def test_retained_namespace_missing_is_refused_not_crashed(self):
        retained = self.fixture.paths.retained
        for entry in retained.iterdir():
            entry.unlink()
        retained.rmdir()
        self.assertRefusedWithoutLedger('Directory missing: .*cache-v2-20261005')

    def test_non_root_link_or_unsafe_directory_refuses(self):
        self.fixture.owner['uid'] = 1000
        self.assertRefusedWithoutLedger('root-owned|Unsafe directory|Image must be')
        self.fixture.owner['uid'] = 0
        self.fixture.paths.gcroots.chmod(0o777)
        try:
            self.assertRefusedWithoutLedger('Unsafe directory')
        finally:
            self.fixture.paths.gcroots.chmod(0o755)

    def test_each_link_must_be_root_owned(self):
        for path in (self.fixture.paths.attached / 'hound-ci-2.service',
                     self.fixture.paths.gcroots / 'hound-ci.slice',
                     self.fixture.paths.enable / 'hound-ci-firewall.service',
                     self.fixture.paths.retained / 'hound-ci-3.service'):
            self.fixture.foreign = {path}
            with self.subTest(path=path):
                self.assertRefusedWithoutLedger('Not a root-owned symlink: ' + str(path))

    def test_inventory_requires_each_named_directory(self):
        with self.assertRaisesRegex(RuntimeError, 'Expected retained directory missing'):
            cap.inventory(self.fixture.paths.gcroots, (cap.RETAINED, 'absent-namespace'))

    def test_image_identity_and_hash_are_pinned(self):
        with patch.object(cap, 'IMAGE_SHA', '0' * 64):
            self.assertRefusedWithoutLedger('Image hash')
        self.fixture.paths.image.chmod(0o644)
        self.assertRefusedWithoutLedger('0444')

    def test_unexpected_root_entry_is_recorded_so_activation_sees_any_later_change(self):
        # An unknown non-link entry in gcroots/hound-ci refuses outright.
        (self.fixture.paths.gcroots / 'stray').mkdir()
        self.assertRefusedWithoutLedger('root-owned symlink')


class BackupCrossTests(unittest.TestCase):
    """The captured ledger is exactly what activation's Backup reads."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.addCleanup(self.fixture.close)
        self.fixture.capture()
        self.backup = act.Backup(self.fixture.manifest())

    def test_activation_accepts_and_checks_the_captured_ledger(self):
        self.backup.profile()
        self.backup.check(set(), {}, False)
        self.assertEqual(set(self.backup.units), {f'hound-ci-{s}.service' for s in act.UNITS})
        self.assertEqual(set(self.backup.retained), {cap.RETAINED})
        # Activation's own root namespace is allowed once roots were created.
        (self.fixture.paths.gcroots / act.ROOT_NAME).mkdir()
        self.backup.check(set(), {}, True)
        with self.assertRaisesRegex(RuntimeError, 'Original link inventory changed'):
            self.backup.check(set(), {}, False)

    def test_replaced_links_must_point_at_the_new_sources(self):
        name = 'hound-ci-1.service'
        new = self.fixture.store / 'new-unit' / name
        new.parent.mkdir()
        new.write_text('new')
        link = self.fixture.paths.attached / name
        link.unlink()
        link.symlink_to(new)
        with self.assertRaisesRegex(RuntimeError, 'link ownership drift'):
            self.backup.check(set(), {}, False)
        self.backup.check({name}, {name: new}, False)

    def test_retained_namespace_drift_holds(self):
        retained = self.fixture.paths.retained
        extra = retained / 'hound-ci-5.service'
        extra.symlink_to(self.fixture.old[1])
        with self.assertRaisesRegex(RuntimeError, 'Original link inventory changed'):
            self.backup.check(set(), {}, False)
        extra.unlink()
        link = retained / 'hound-ci-2.service'
        link.unlink()
        link.symlink_to(self.fixture.old[3])
        with self.assertRaisesRegex(RuntimeError, 'link ownership drift'):
            self.backup.check(set(), {}, False)
        link.unlink()
        link.symlink_to(self.fixture.old[2])
        self.backup.check(set(), {}, False)

    def test_retained_namespace_removed_holds(self):
        retained = self.fixture.paths.retained
        for entry in retained.iterdir():
            entry.unlink()
        retained.rmdir()
        with self.assertRaisesRegex(RuntimeError, 'Retained root directory missing'):
            self.backup.check(set(), {}, False)

    def test_retained_inventory_is_required_and_cannot_claim_activations_namespace(self):
        manifest = self.fixture.manifest()
        for value in (None, [], {act.ROOT_NAME: []}, {'Bad/Name': []}, {cap.RETAINED: {}}):
            changed = dict(manifest, retained_root_directories=value)
            if value is None:
                changed.pop('retained_root_directories')
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'Retained GC-root directory'):
                act.Backup(changed)

    def test_ledger_tampering_after_preflight_holds(self):
        path = self.fixture.paths.backup / 'rollback-manifest.json'
        path.chmod(0o600)
        value = json.loads(path.read_text())
        value['captured_utc'] = '2026-10-06T00:00:00+00:00'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, 'Rollback manifest changed'):
            self.backup.check(set(), {}, False)

    def test_copy_or_target_content_drift_holds(self):
        copy = self.fixture.paths.backup / 'hound-ci-4.service'
        copy.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'no longer preserved'):
            self.backup.check(set(), {}, False)
        with self.assertRaisesRegex(RuntimeError, 'hash/copy'):
            act.Backup(self.fixture.manifest())


if __name__ == '__main__':
    unittest.main()
