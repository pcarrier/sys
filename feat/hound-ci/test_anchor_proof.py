#!/usr/bin/env python3
"""Host-free tests of anchor-proof.py over test_activate's fixture manager."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

here = Path(__file__).parent
spec = importlib.util.spec_from_file_location('test_activate_fixture', here / 'test_activate.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
act, effect = fixtures.act, fixtures.effect
spec = importlib.util.spec_from_file_location('anchor_proof', here / 'anchor-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

ORIGINAL = {slot: fixtures.original_invocation(slot) for slot in act.UNITS}


class AnchorProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = fixtures.Fixture(self.temp.name)
        self.structure = self.fixture.window['effect_structure_sha256']
        # The manual completion's state: new links, holds unlinked, ONE reload.
        for slot in act.UNITS:
            name = f'hound-ci-{slot}.service'
            link = act.ATTACHED / name
            link.unlink()
            link.symlink_to(Path(act.UNITS[slot]) / name)
            (act.RUNTIME / (name + '.d') / act.DROPIN).unlink()
        self.fixture.run(['systemctl', 'daemon-reload'])
        self.fixture.commands.clear()

    def tearDown(self):
        self.fixture.close()
        self.temp.cleanup()

    def prove(self, slot, structure=None, original=None):
        return proof.prove_anchor(act, effect, slot, original or ORIGINAL, structure or self.structure, [])

    def start(self, slot):
        self.fixture.run(['systemctl', '--job-mode=fail', 'start', '--', f'hound-ci-{slot}.service'])

    def test_each_slot_in_order_proves_then_starts_and_the_proof_runs_nothing(self):
        for slot in act.UNITS:
            result = self.prove(slot)
            self.assertEqual((result['unit'], result['structure']), (f'hound-ci-{slot}.service', self.structure))
            self.assertEqual(result['final_effect_sha256'],
                             hashlib.sha256(act.canonical([[f'hound-ci-{slot}.service', 'START']])).hexdigest())
            self.assertEqual(self.fixture.commands, [])  # read-only: no reload, no start
            self.start(slot)
            self.fixture.commands.clear()

    def test_structure_other_than_the_lease_is_refused_and_named(self):
        with self.assertRaisesRegex(RuntimeError, f'Effect structure {self.structure} differs from the lease 0{{64}}'):
            self.prove(1, structure='0' * 64)

    def test_order_and_state_preconditions(self):
        with self.assertRaisesRegex(RuntimeError, 'not positively running'):
            self.prove(2)  # slot 1 not started yet
        self.start(1)
        with self.assertRaisesRegex(RuntimeError, 'Controller is not stopped'):
            self.prove(1)  # already running: never dispatched again
        self.fixture.manager['hound-ci-3.service']['InvocationID'] = 'd' * 32
        with self.assertRaisesRegex(RuntimeError, 'lost its ORIGINAL invocation'):
            self.prove(2)
        self.fixture.manager['hound-ci-1.service']['InvocationID'] = ORIGINAL[1]
        self.fixture.manager['hound-ci-3.service']['InvocationID'] = ORIGINAL[3]
        with self.assertRaisesRegex(RuntimeError, 'kept its ORIGINAL invocation'):
            self.prove(2)

    def test_held_or_old_units_are_refused(self):
        held = deepcopy(self.fixture.manager['hound-ci-2.service'])
        self.fixture.manager['hound-ci-2.service'] = fixtures.loaded(2, new=True, held=True)
        with self.assertRaisesRegex(RuntimeError, 'restart policy drift'):
            self.prove(1)
        self.fixture.manager['hound-ci-2.service'] = fixtures.loaded(2, new=False, held=False)
        with self.assertRaisesRegex(RuntimeError, 'executable/full argv'):
            self.prove(1)
        self.fixture.manager['hound-ci-2.service'] = held
        self.prove(1)

    def test_queued_job_in_the_closure_or_graph_drift_is_refused(self):
        self.fixture.jobs = [[3, 'hound-ci-image.service', 'start', 'waiting', '/job/3', '/unit']]
        with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
            self.prove(1)
        self.fixture.jobs = []
        self.fixture.dependencies['hound-ci-image.service']['Wants'] = 'extra.service'
        self.fixture.dependencies['extra.service'] = {'Id': 'extra.service', 'LoadState': 'loaded', 'ActiveState': 'active',
                                                      'Requires': '', 'Wants': '', 'Requisite': '', 'BindsTo': ''}
        with self.assertRaisesRegex(RuntimeError, 'differs from the lease'):
            self.prove(1)

    def test_original_invocations_must_be_four_distinct(self):
        with self.assertRaisesRegex(RuntimeError, 'Four distinct'):
            self.prove(1, original={1: ORIGINAL[1], 2: ORIGINAL[2], 3: ORIGINAL[3]})
        with self.assertRaisesRegex(RuntimeError, 'Four distinct'):
            self.prove(1, original={**ORIGINAL, 4: ORIGINAL[1]})
        self.assertEqual(proof.originals([f'{n}={ORIGINAL[n]}' for n in ORIGINAL]), ORIGINAL)
        for bad in (['1=xyz'], [f'1={ORIGINAL[1]}', f'1={ORIGINAL[2]}'], [f'0x1={ORIGINAL[1]}']):
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, 'ANCHOR_PROOF_REFUSED'):
                proof.originals(bad)


class PinnedLoadTests(unittest.TestCase):
    def test_only_direct_store_sources_with_their_exact_sha_load(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(proof, 'STORE', Path(folder)):
            source = Path(folder) / 'source.py'
            source.write_bytes(b'VALUE = 42\n')
            source.chmod(0o444)
            digest = hashlib.sha256(b'VALUE = 42\n').hexdigest()
            self.assertEqual(proof.load_pinned(source, digest, 'pinned').VALUE, 42)
            for path, sha, message in ((source, '0' * 64, 'differs from its cleared SHA'),
                                       (source, 'short', 'SHA-256 pin'),
                                       (Path(folder) / 'sub' / 'source.py', digest, 'direct /nix/store')):
                with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                    proof.load_pinned(path, sha, 'pinned')
            source.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, 'immutable'):
                proof.load_pinned(source, digest, 'pinned')
            link = Path(folder) / 'link.py'
            link.symlink_to(source)
            with self.assertRaises(OSError):
                proof.load_pinned(link, digest, 'pinned')  # O_NOFOLLOW


if __name__ == '__main__':
    unittest.main()
