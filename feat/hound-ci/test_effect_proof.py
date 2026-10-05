#!/usr/bin/env python3
"""Host-free theorem regressions; no manager/root/API/signal access."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('effect', Path(__file__).with_name('effect-proof.py'))
effect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(effect)


def unit(name, state='inactive', **changes):
    result = {key: [] for key in effect.UNIT_ARRAYS}
    result.update({key: '' for key in effect.UNIT_STRINGS})
    result.update({key: False for key in effect.UNIT_BOOLS})
    result.update(Id=name, Names=[name], FollowingSet=[], LoadState='loaded', ActiveState=state,
                  SubState='dead' if state == 'inactive' else 'running', FreezerState='running',
                  Job=[0, '/'], FailureAction='none', SuccessAction='none',
                  StartLimitAction='none', JobTimeoutAction='none')
    result.update(changes)
    return result


class EffectTests(unittest.TestCase):
    def prove(self, units, jobs=(), ignored=()):
        aliases = {alias: name for name, value in units.items() for alias in value['Names']}
        return effect.prove('a.service', lambda name: deepcopy(units[aliases[name]]), jobs, ignored)

    def test_trivial_and_active_barrier_gc_inactive_boot_chain(self):
        graph = {'a.service': unit('a.service', Requires=['p.target']),
                 'p.target': unit('p.target', 'active', Wants=['boot.service']),
                 'boot.service': unit('boot.service', Wants=['late.service']),
                 'late.service': unit('late.service')}
        proof = self.prove(graph)
        self.assertEqual(proof['first_pass_redundant_units'], ['p.target'])
        self.assertEqual(proof['gc_order'], [['boot.service', 'START'], ['late.service', 'START']])
        self.assertEqual(proof['final_effect'], [['a.service', 'START']])

    def test_disconnected_inactive_cycle_after_active_barrier_is_NOT_gc(self):
        graph = {'a.service': unit('a.service', Requires=['p.target']),
                 'p.target': unit('p.target', 'active', Wants=['x.service']),
                 'x.service': unit('x.service', Wants=['y.service']),
                 'y.service': unit('y.service', Requires=['x.service'])}
        with self.assertRaisesRegex(RuntimeError, 'residual dependency cycle'):
            self.prove(graph)

    def test_mixed_active_start_stop_barrier_cannot_cut_ancestors(self):
        graph = {'a.service': unit('a.service', Requires=['p.target'], Conflicts=['p.target']),
                 'p.target': unit('p.target', 'active', Wants=['x.service']),
                 'x.service': unit('x.service')}
        with self.assertRaisesRegex(RuntimeError, 'mixed unreducible'):
            self.prove(graph)
        graph['p.target']['ActiveState'] = 'inactive'
        with self.assertRaisesRegex(RuntimeError, 'mixed unreducible'):
            self.prove(graph)

    def test_anchored_inactive_start_and_verify_rejected_no_repair(self):
        for relation in ('Requires', 'BindsTo', 'Wants', 'Upholds', 'Requisite'):
            graph = {'a.service': unit('a.service', **{relation: ['x.service']}),
                     'x.service': unit('x.service')}
            with self.subTest(relation=relation), self.assertRaises(RuntimeError):
                self.prove(graph)
        graph = {'a.service': unit('a.service', Requisite=['x.service']),
                 'x.service': unit('x.service', 'active')}
        self.prove(graph)

    def test_conflicts_both_directions_and_exact_inverse_stop_atoms(self):
        for conflict in ('Conflicts', 'ConflictedBy'):
            for inverse in ('RequiredBy', 'RequisiteOf', 'BoundBy', 'ConsistsOf'):
                graph = {'a.service': unit('a.service', **{conflict: ['shutdown.target']}),
                         'shutdown.target': unit('shutdown.target', **{inverse: ['x.service']}),
                         'x.service': unit('x.service', 'active')}
                # inactive STOP barrier is cut, downstream active STOP garbage
                # collected. A queued x job still blocks COMPLETE effect closure.
                proof = self.prove(graph)
                self.assertIn(['x.service', 'STOP'], proof['prospective_jobs'])
                with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
                    self.prove(graph, [[1, 'x.service', 'reload', 'waiting', '/job', '/unit']])
                graph['shutdown.target']['ActiveState'] = 'active'
                with self.assertRaisesRegex(RuntimeError, 'anchored nonanchor'):
                    self.prove(graph)

    def test_every_existing_job_type_blocks_even_redundant_ancestor_or_nop(self):
        graph = {'a.service': unit('a.service', Requires=['p.target']),
                 'p.target': unit('p.target', 'active', Wants=['x.service']),
                 'x.service': unit('x.service')}
        for kind in ('start', 'stop', 'reload', 'restart', 'reload-or-start', 'verify-active', 'nop', 'try-restart'):
            for name in ('a.service', 'p.target', 'x.service'):
                with self.subTest(kind=kind, name=name), self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
                    self.prove(graph, [[42, name, kind, 'running', '/job', '/unit']])
        self.prove(graph, [[42, 'unrelated.service', 'reload', 'running', '/job', '/unit']])

    def test_alias_dash_mount_and_escaped_quoted_text_unit_names(self):
        graph = {'a.service': unit('a.service', Requires=['alias.service', '-.mount', 'quoted\\x20name.mount']),
                 'p.service': unit('p.service', 'active', Names=['p.service', 'alias.service']),
                 '-.mount': unit('-.mount', 'active'),
                 'quoted\\x20name.mount': unit('quoted\\x20name.mount', 'active')}
        proof = self.prove(graph)
        self.assertEqual(proof['aliases']['alias.service'], 'p.service')
        with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
            self.prove(graph, [[42, 'alias.service', 'start', 'waiting', '/job', '/unit']])
        for invalid in ("'-.mount'", 'quoted name.mount', '-.mount --failed'):
            with self.assertRaisesRegex(RuntimeError, 'invalid exact unit ID'):
                effect.unit_name(invalid)

    def test_following_members_jobs_and_dependencies_not_collapsed_into_alias(self):
        graph = {'a.service': unit('a.service', Requires=['leader.device']),
                 'leader.device': unit('leader.device', 'active', FollowingSet=['other.device']),
                 'other.device': unit('other.device', 'active', Following='leader.device',
                                      FollowingSet=['leader.device'], Wants=['x.service']),
                 'x.service': unit('x.service')}
        # Following creates a job dependency cycle, but every device job is
        # independently first-pass redundant, so both are legitimately cut.
        proof = self.prove(graph)
        self.assertIn(['other.device', 'START'], proof['prospective_jobs'])
        with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
            self.prove(graph, [[1, 'other.device', 'nop', 'waiting', '/job', '/unit']])

    def test_unknown_unbounded_unstable_and_graceful_restart_hold(self):
        graph = {'a.service': unit('a.service', Wants=['x.service']),
                 'x.service': unit('x.service', LoadState='not-found')}
        with self.assertRaisesRegex(RuntimeError, 'unknown/unloaded'):
            self.prove(graph)
        graph['x.service'] = unit('x.service', 'activating')
        with self.assertRaisesRegex(RuntimeError, 'unstable'):
            self.prove(graph)
        graph = {'a.service': unit('a.service', Conflicts=['shutdown.target']),
                 'shutdown.target': unit('shutdown.target', PropagatesStopTo=['x.service']),
                 'x.service': unit('x.service')}
        with self.assertRaisesRegex(RuntimeError, 'graceful STOP'):
            self.prove(graph)

    def test_known_ignored_enoent_needs_explicit_allowlist_and_only_wants(self):
        graph = {'a.service': unit('a.service', Wants=['x.service']),
                 'x.service': unit('x.service', LoadState='not-found',
                                   LoadError=['org.freedesktop.systemd1.NoSuchUnit', 'not found'])}
        with self.assertRaisesRegex(RuntimeError, 'unknown/unloaded'):
            self.prove(graph)
        proof = self.prove(graph, ignored=['x.service'])
        self.assertEqual(proof['ignored_enoent'][0]['rule'], 'ignored-START-ENOENT-v261-1065:1075')
        graph['a.service']['Requires'] = ['x.service']
        with self.assertRaisesRegex(RuntimeError, 'unknown/unloaded'):
            self.prove(graph, ignored=['x.service'])

    def test_known_not_found_stop_is_a_real_job_with_complete_inverse_closure(self):
        graph = {'a.service': unit('a.service', Conflicts=['missing.target']),
                 'missing.target': unit('missing.target', LoadState='not-found',
                       LoadError=['org.freedesktop.systemd1.NoSuchUnit', 'Unit not found.'],
                       RequiredBy=['x.service']),
                 'x.service': unit('x.service', 'active')}
        proof = self.prove(graph)
        self.assertIn(['missing.target', 'STOP'], proof['prospective_jobs'])
        self.assertIn(['x.service', 'STOP'], proof['prospective_jobs'])
        self.assertEqual(proof['known_not_found_stop_units'], ['missing.target'])
        self.assertEqual(proof['first_pass_redundant_units'], ['missing.target'])
        with self.assertRaisesRegex(RuntimeError, 'existing job ANY type'):
            self.prove(graph, [[1, 'x.service', 'reload', 'waiting', '/job', '/unit']])
        graph['a.service']['Requires'] = ['missing.target']
        with self.assertRaisesRegex(RuntimeError, 'unknown/unloaded'):
            self.prove(graph)

    def test_perpetual_stop_not_applicable_never_manufactures_mixed_mount_jobs(self):
        graph = {'a.service': unit('a.service', Requires=['-.mount'], Conflicts=['-.mount']),
                 '-.mount': unit('-.mount', 'active', Perpetual=True)}
        proof = self.prove(graph)
        self.assertNotIn(['-.mount', 'STOP'], proof['prospective_jobs'])
        self.assertEqual(proof['not_applicable_stop'][0]['rule'], 'STOP-EBADR-Perpetual-unit.c3037:3040')

    def test_no_runtime_handlers_extra_activation_actions_or_false_summary(self):
        for key, value in (('OnFailure', ['repair.service']), ('OnSuccess', ['repair.service']),
                           ('Upholds', ['x.service']), ('Triggers', ['x.service']),
                           ('FailureAction', 'reboot'), ('StopWhenUnneeded', True)):
            graph = {'a.service': unit('a.service', **{key: value}),
                     'repair.service': unit('repair.service'), 'x.service': unit('x.service', 'active')}
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'anchor'):
                self.prove(graph)
        graph = {'a.service': unit('a.service')}
        del graph['a.service']['ConflictedBy']
        with self.assertRaisesRegex(RuntimeError, 'typed array'):
            self.prove(graph)

    def test_cycle_and_effect_bounds_fail_closed(self):
        graph = {'a.service': unit('a.service', Requires=['a.service'])}
        with self.assertRaisesRegex(RuntimeError, 'residual dependency cycle'):
            self.prove(graph)
        graph = {'a.service': unit('a.service', Requires=['p.target']), 'p.target': unit('p.target', 'active')}
        from unittest.mock import patch
        with patch.object(effect, 'MAX_UNITS', 1), self.assertRaisesRegex(RuntimeError, 'closure bound'):
            self.prove(graph)



class HostSourceQualificationTests(unittest.TestCase):
    def test_host_261_2_effect_files_equal_v261_pins_and_are_unpatched(self):
        import json
        data = json.loads(Path(__file__).with_name('systemd-261.2-qualification.json').read_text())
        files = data['effect_proof_files']
        self.assertEqual(set(files), set(effect.SOURCE_SHA256))
        for name, row in files.items():
            with self.subTest(file=name):
                self.assertEqual(row['v261_sha256'], effect.SOURCE_SHA256[name])
                self.assertEqual(row['host_src_sha256'], effect.SOURCE_SHA256[name])
                self.assertIs(row['equal'], True)
                self.assertIs(row['touched_by_patch'], False)
                self.assertIs(row['touched_by_postPatch'], False)
                for patch in data['patches'].values():
                    self.assertNotIn('src/core/' + name, patch['files'])
        self.assertEqual(data['source']['version'], '261.2')
        self.assertEqual(data['pid1']['sha256'], '448f82f2920f1f3d4919981565d4663b19b60e13b0d0dfacd2f2b9641c883a0c')


if __name__ == '__main__':
    unittest.main()
