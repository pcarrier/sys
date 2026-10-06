#!/usr/bin/env python3
"""Generation main-slot-20261006: every helper names the same state, witness,
old controllers, new units, labels, image and rollback ledger. Source-only."""
import importlib.util
from pathlib import Path
import re
import unittest

HERE = Path(__file__).parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drain = load('drain', 'drain-old.py')
gate = load('gate', 'drain-gh-gate.py')
wait = load('wait', 'wait-drained.py')
finish = load('finish', 'finish-drain.py')
act = load('act', 'activate-cache-v2.py')
cap = load('cap', 'capture-rollback.py')
GENERATION = 'main-slot-20261006'
MAIN_ONLY = ['self-hosted', 'Linux', 'X64', 'hound-ci-main']
SHARED = ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main']


class GenerationTests(unittest.TestCase):
    def test_state_and_ledger_paths(self):
        state = Path('/var/lib/hound-ci/rollout-' + GENERATION)
        for module in (drain, gate, wait, finish, act):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.STATE, state)
        self.assertEqual(act.BACKUP, cap.BACKUP)
        self.assertEqual(act.BACKUP, Path('/var/lib/hound-ci/rollout-main-slot-backup-20261006'))
        self.assertEqual((act.ROOT_NAME, cap.GENERATION), (GENERATION, GENERATION))
        self.assertNotEqual(act.ROOT_NAME, cap.RETAINED)
        # Never the previous generation's state (its activation.json must not resume).
        for module in (drain, gate, wait, finish, act):
            self.assertNotIn('20261005', str(module.STATE))
        self.assertEqual((act.ATTACHED, act.GCROOTS, act.ENABLE), (cap.ATTACHED, cap.GCROOTS, cap.ENABLE))

    def test_witness_is_pierres_go_ahead(self):
        for module in (drain, wait, finish):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.WITNESS_SINCE, '2026-10-06T11:45:00+00:00')

    def test_old_controllers_are_the_loaded_cache_v2_ones(self):
        self.assertEqual(drain.OLD_SOURCE, wait.OLD_SOURCE)
        self.assertEqual(drain.OLD_SOURCE, finish.OLD_SOURCE)
        self.assertEqual(drain.OLD_SOURCE_SHA, wait.OLD_SOURCE_SHA256)
        self.assertEqual(drain.OLD_SOURCE_SHA, finish.OLD_SOURCE_SHA256)
        self.assertEqual(drain.OLD_IMAGE_ARGS, act.IMAGE_ARGS)
        self.assertEqual(act.IMAGE_ARGS, ['--image', Path(act.OLD_IMAGE).name])

    def test_image_does_not_change(self):
        self.assertEqual(str(act.CANDIDATE), finish.CANDIDATE)
        self.assertEqual(act.CANDIDATE, act.OLD_IMAGE)
        self.assertEqual(act.OLD_IMAGE, cap.IMAGE)
        for sha in (act.OLD_SHA, finish.CANDIDATE_SHA, cap.IMAGE_SHA):
            self.assertEqual(sha, act.CANDIDATE_SHA)

    def test_new_units_and_labels(self):
        self.assertEqual(act.UNITS, finish.NEW_UNITS)
        self.assertEqual(act.LABELS, finish.NEW_LABELS)
        self.assertEqual(act.LABELS, {1: SHARED, 2: SHARED, 3: SHARED, 4: MAIN_ONLY})
        for slot, path in act.UNITS.items():
            self.assertRegex(path, rf'^/nix/store/[0-9a-z]{{32}}-unit-hound-ci-{slot}\.service$')
        self.assertTrue(set(act.UNITS.values()).isdisjoint(cap.OLD.values()))

    def test_capture_records_the_units_drain_retires(self):
        self.assertEqual(set(cap.OLD), set(act.UNITS))
        for slot, path in cap.OLD.items():
            self.assertRegex(path, rf'^/nix/store/[0-9a-z]{{32}}-unit-hound-ci-{slot}\.service$')
        self.assertEqual(cap.RETAINED, 'cache-v2-20261005')

    def test_supervisor_allowlist_covers_every_label(self):
        supervisor = load('supervisor', 'supervisor.py')
        for labels in act.LABELS.values():
            self.assertEqual(supervisor.runner_labels(labels), labels)
        self.assertNotIn('hound-ci-main', supervisor.DEFAULT_LABELS)

    def test_nix_module_labels_match(self):
        source = (HERE.parent / 'hound-ci.nix').read_text()
        for label in ('hound-ci', 'hound-ci-main'):
            self.assertIn(f'"{label}"', source)
        self.assertRegex((HERE.parent.parent / 'hosts' / 'hound.nix').read_text(),
                         r'reservedMainSlots\s*=\s*1;')


if __name__ == '__main__':
    unittest.main()
