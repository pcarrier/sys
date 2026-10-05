#!/usr/bin/env python3
"""Host-free seed tests; never use Docker, register runners, or fetch artifacts."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cache', Path(__file__).with_name('cache.py'))
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)
PINS = cache.load_json(Path(__file__).with_name('cache-pins.json'))


class CacheTests(unittest.TestCase):
    def test_exact_bounded_pins(self):
        self.assertIs(cache.validate_pins(PINS), PINS)
        for mutation in (
            lambda p: p.update(schema=2),
            lambda p: p.update(source_commit='main'),
            lambda p: p['nodes'][0].update(version='24/../../root'),
            lambda p: p['nodes'][0].update(sha256='00'),
            lambda p: p['images'][0].update(tag='attacker/image:latest'),
            lambda p: p['images'][0].update(digest='latest'),
            lambda p: p['images'][0].update(compressed_bytes=4 * 1024 ** 3),
            lambda p: p['fixtures'].append(p['fixtures'][0]),
        ):
            pins = copy.deepcopy(PINS)
            mutation(pins)
            with self.assertRaises(ValueError):
                cache.validate_pins(pins)

    def test_json_bounds_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'pins.json'
            path.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(ValueError):
                cache.load_json(path)
            path.write_text(' ' * 65537)
            with self.assertRaises(ValueError):
                cache.load_json(path)

    def test_archive_traversal_symlink_device_duplicate_and_size(self):
        root_name = 'node-v24.21.0-linux-x64'
        cases = [('../outside', 'file'), (root_name+'/../../outside', 'file'),
                 (root_name+'/evil', 'symlink'), (root_name+'/device', 'device'),
                 (root_name+'/duplicate', 'duplicate'), (root_name+'/big', 'oversize')]
        for name, kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                folder = Path(root)
                path = folder/'archive.tar.xz'
                with tarfile.open(path, 'w:xz') as tar:
                    info = tarfile.TarInfo(name)
                    if kind == 'symlink':
                        info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'
                    elif kind == 'device':
                        info.type = tarfile.CHRTYPE
                    elif kind == 'oversize':
                        # A symlink can carry a declared length without allocating
                        # huge test files; validation checks it before extraction.
                        info.type = tarfile.SYMTYPE; info.linkname = 'safe'; info.size = 257*1024**2
                    else:
                        info.size = 1
                    tar.addfile(info, io.BytesIO(b'x') if info.isfile() else None)
                    if kind == 'duplicate':
                        tar.addfile(info, io.BytesIO(b'x'))
                stage = folder/'stage'; stage.mkdir()
                with self.assertRaises((ValueError, tarfile.TarError)):
                    cache.extract_node(path, stage, '24.21.0')
                self.assertFalse((folder/'outside').exists())

    def test_valid_archive_strips_special_permissions_and_internal_links(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root); archive = folder/'node.tar.xz'
            with tarfile.open(archive, 'w:xz') as tar:
                info = tarfile.TarInfo('node-v24.21.0-linux-x64/bin/node')
                info.mode = 0o6755; info.size = 1
                tar.addfile(info, io.BytesIO(b'x'))
                link = tarfile.TarInfo('node-v24.21.0-linux-x64/bin/npm')
                link.type = tarfile.SYMTYPE; link.linkname = 'node'
                tar.addfile(link)
            stage = folder/'stage'; stage.mkdir()
            extracted = cache.extract_node(archive, stage, '24.21.0')
            self.assertEqual((extracted/'bin/node').stat().st_mode & 0o6000, 0)
            self.assertEqual((extracted/'bin/npm').read_bytes(), b'x')

    def test_artifact_length_and_hash_gate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'download'
            def fake_command(argv):
                path.write_bytes(b'wrong')
            with patch.object(cache, 'command', side_effect=fake_command):
                with self.assertRaises(ValueError):
                    cache.fetch('https://nodejs.org/dist/v24.21.0/a', path, '0'*64, 96*1024**2)
                with self.assertRaises(ValueError):
                    cache.fetch('http://example.com/unsafe', path, '0'*64, 96*1024**2)

    def test_partial_node_cache_refused_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root); (folder/'node/24.21.0').mkdir(parents=True)
            with patch.object(cache, 'TOOLCACHE', folder), patch.object(cache.shutil, 'chown'), patch.object(cache, 'fetch') as fetch:
                with self.assertRaises(ValueError):
                    cache.install_nodes(PINS, folder)
                fetch.assert_not_called()

    def test_recipe_context_contract(self):
        cache.validate_recipe(b'FROM debian:bookworm-slim\nRUN true\nWORKDIR /workspace\n')
        for recipe in (b'FROM attacker:latest\n', b'FROM ubuntu:24.04\nCOPY secret /root/\n',
                       b'FROM ubuntu:24.04\nADD https://example.com/a /a\n',
                       b'# syntax=attacker/frontend\nFROM ubuntu:24.04\n',
                       b'FROM ubuntu:24.04\nFROM attacker:latest\n'):
            with self.assertRaises(ValueError):
                cache.validate_recipe(recipe)

    def test_native_status_fail_closed(self):
        good = '\n'.join(f'{p}\tinstall ok installed\t1' for p in cache.NATIVE_PACKAGES)
        with patch.object(cache, 'output', return_value=good):
            self.assertEqual(set(cache.native_ledger()), set(cache.NATIVE_PACKAGES))
        for bad in (good.replace('install ok installed', 'deinstall ok config-files',1), good.rsplit('\n',1)[0]):
            with patch.object(cache, 'output', return_value=bad):
                with self.assertRaises(ValueError):
                    cache.native_ledger()

    def test_preflight_no_download_and_mismatched_contract_rejected(self):
        with patch.object(cache, 'load_json', return_value={'schema':2}), patch.object(cache, 'fetch') as fetch:
            with self.assertRaises(ValueError):
                cache.preflight(PINS, 'pins')
            fetch.assert_not_called()

    def test_trusted_private_repo_snapshots_verified_without_credentials(self):
        recipes=cache.recipe_bytes(PINS,Path(__file__).with_name('fixture-recipes'))
        self.assertEqual(set(recipes),{'browser','yas'})
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            for name,data in recipes.items():(folder/(name+'.Dockerfile')).write_bytes(data)
            (folder/'yas.Dockerfile').write_bytes(recipes['yas']+b'# changed PR recipe\n')
            with self.assertRaises(ValueError):cache.recipe_bytes(PINS,folder)

    def test_registry_attestations_and_sizes_verified(self):
        import hashlib
        layer = {'size': 123, 'digest': 'sha256:' + '3'*64}
        child = json.dumps({'schemaVersion': 2, 'layers': [layer]}).encode()
        child_digest = 'sha256:' + hashlib.sha256(child).hexdigest()
        index = json.dumps({'schemaVersion': 2, 'manifests': [{'digest': child_digest, 'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
        pin = {'tag': 'node:24', 'digest': child_digest, 'manifest_sha256': hashlib.sha256(index).hexdigest(), 'compressed_bytes': 123}
        replies = [json.dumps({'token':'anonymous.public.token'}).encode(), index, child]
        for update in ({}, {'compressed_bytes':124}, {'manifest_sha256':'0'*64}, {'digest':'sha256:'+'0'*64}):
            with patch.object(cache, 'command', side_effect=[type('R',(),{'stdout':reply}) for reply in replies]):
                if update:
                    with self.assertRaises(ValueError):
                        cache.registry_metadata(pin | update)
                else:
                    cache.registry_metadata(pin)

    def test_archive_member_bounds_are_incremental(self):
        fake = type('Archive', (), {})()
        def entries():
            for index in range(20002):
                if index == 20001:
                    raise AssertionError('Read beyond early archive count bound')
                entry = tarfile.TarInfo(f'node-v24.21.0-linux-x64/f{index}')
                yield entry
        class Context:
            def __enter__(self): return entries()
            def __exit__(self,*args): pass
        with patch.object(cache.tarfile, 'open', return_value=Context()):
            with self.assertRaises(ValueError):
                cache.extract_node('unused', Path('/unused'), '24.21.0')

    def test_worker_explicit_environment_not_marker_only(self):
        guest=Path(__file__).with_name('guest.sh').read_text()
        self.assertIn('env -i HOME=/home/runner USER=runner LOGNAME=runner',guest)
        self.assertIn('RUNNER_TOOL_CACHE="$RUNNER_TOOL_CACHE" ./run.sh',guest)
        self.assertIn('/opt/hound-ci-cache.py preflight',guest)
        builder=Path(__file__).with_name('cache.py').read_text()
        self.assertIn("'docker.service', 'docker.socket', 'containerd.service'",builder)
        self.assertNotIn('docker login',builder)
        self.assertNotIn('registry/cache',builder)


if __name__ == '__main__':
    unittest.main()
