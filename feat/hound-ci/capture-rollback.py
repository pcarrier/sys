#!/usr/bin/env python3
"""Capture the schema-2 rollback ledger for generation main-slot-20261006. Root, write-once.

Before the drain: records the four loaded cache-v2 unit links (and a 0600 copy
of each target), the host profile, the immutable image (hashed), and exact
inventories of gcroots/hound-ci (its links, and the 10-06 rollout's retained
cache-v2-20261005 namespace) and multi-user.target.wants, in the format
activate-cache-v2.py's Backup reads. It changes no unit, link, root or service:
it only creates BACKUP (refused if it exists) and files inside it. An
interrupted capture leaves BACKUP without rollback-manifest.json: reconcile by
hand (inspect, then remove it), never rerun over it.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

PINNED_PYTHON = '/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3'
GENERATION = 'main-slot-20261006'
BACKUP = Path('/var/lib/hound-ci/rollout-main-slot-backup-20261006')
ATTACHED = Path('/etc/systemd/system.attached')
GCROOTS = Path('/nix/var/nix/gcroots/hound-ci')
ENABLE = ATTACHED / 'multi-user.target.wants'
CURRENT = Path('/run/current-system')
PROFILE = '/nix/store/0yjgryijva9ir0x4y2fhkj9pfw3ikv77-nixos-system-hound-26.11.20260922.6774f7b'
IMAGE = Path('/var/lib/hound-ci/base-cache-v2.qcow2')
IMAGE_SHA = 'daf2ab773887c98d9b8ac107a6cfcce9db450364d55fa7645ee46a873805296b'
# The units loaded since the 10-06 rollout (activation's old units).
OLD = {1: '/nix/store/pahfqs4j2ynghvh356qjxv5mwx48mk0n-unit-hound-ci-1.service',
       2: '/nix/store/0rvyj9c7i52yy7yw7iabcah6v8g4pkfs-unit-hound-ci-2.service',
       3: '/nix/store/gq43ybz4c5hwvww1w7jxplkbk62xhm64-unit-hound-ci-3.service',
       4: '/nix/store/rkmx5h7g74pk8qg9li1hhz91adirmdby-unit-hound-ci-4.service'}
RETAINED = 'cache-v2-20261005'  # their GC roots, from the 10-06 rollout
ROOT_UID = ROOT_GID = 0
STORE = '/nix/store'
UNIT_LIMIT = 256 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError('CAPTURE_REFUSED ' + message)


def root_owned(meta):
    return meta.st_uid == ROOT_UID and meta.st_gid == ROOT_GID


def read_store_file(path):
    path = Path(path)
    require(path.is_absolute() and path.is_relative_to(STORE) and path.resolve(strict=True) == path,
            f'Non-canonical store file: {path}')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and not meta.st_mode & 0o222 and 0 < meta.st_size <= UNIT_LIMIT,
                f'Not an immutable bounded store file: {path}')
        data = os.read(fd, UNIT_LIMIT + 1)
        require(len(data) == meta.st_size, f'Store file changed while read: {path}')
        return data
    finally:
        os.close(fd)


def link(path, store_target=True):
    meta = path.lstat()
    require(stat.S_ISLNK(meta.st_mode) and root_owned(meta), f'Not a root-owned symlink: {path}')
    target = os.readlink(path)
    if store_target:
        require(target.startswith(STORE + '/') and os.path.exists(target), f'Link target missing/not in the store: {path}')
    return {'path': str(path), 'target': target, 'uid': meta.st_uid, 'gid': meta.st_gid}


def inventory(folder, directories=()):
    require(os.path.lexists(folder), f'Directory missing: {folder}')
    meta = folder.lstat()
    require(stat.S_ISDIR(meta.st_mode) and root_owned(meta) and not meta.st_mode & 0o022, f'Unsafe directory: {folder}')
    links, found = [], set()
    for entry in sorted(folder.iterdir()):
        if entry.name in directories:
            found.add(entry.name)
            continue
        links.append(link(entry))
    require(found == set(directories), f'Expected retained directory missing in {folder}')
    return links


def hash_image():
    fd = os.open(IMAGE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and root_owned(before) and stat.S_IMODE(before.st_mode) == 0o444,
                'Image must be a root:root 0444 regular file')
        digest = hashlib.sha256()
        while block := os.read(fd, 8 * 1024 * 1024):
            digest.update(block)
        after = os.fstat(fd)
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
                digest.hexdigest() == IMAGE_SHA, 'Image hash/identity differs from the qualified pin')
    finally:
        os.close(fd)
    return {'path': str(IMAGE), 'sha256': IMAGE_SHA}


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def capture():
    require(os.path.realpath(sys.executable) == os.path.realpath(PINNED_PYTHON) and
            sys.flags.isolated and sys.flags.dont_write_bytecode, 'Run with the pinned Nix Python -I -B')
    require(os.geteuid() == ROOT_UID, 'Root operator required')
    require(not os.path.lexists(BACKUP), 'Rollback ledger directory exists: never recapture over it')
    require(os.readlink(CURRENT) == PROFILE, 'Host profile differs from the pinned baseline')
    units = []
    contents = {}
    for slot, store in OLD.items():
        name = f'hound-ci-{slot}.service'
        entry = link(ATTACHED / name)
        require(entry['target'] == f'{store}/{name}', f'{name} is not linked to the loaded cache-v2 unit')
        contents[name] = read_store_file(entry['target'])
        units.append(dict(entry, unit=name, sha256=hashlib.sha256(contents[name]).hexdigest(), backup=str(BACKUP / name)))
    retained = inventory(GCROOTS / RETAINED)
    require(sorted((Path(e['path']).name, e['target']) for e in retained) ==
            sorted((f'hound-ci-{slot}.service', store) for slot, store in OLD.items()),
            'Retained cache-v2 GC roots differ from the loaded units')
    manifest = {
        'schema': 2, 'generation': GENERATION, 'host_profile': PROFILE,
        'host_profile_resolved': str(CURRENT.resolve(strict=True)), 'old_image': hash_image(),
        'old_unit_links': units, str(GCROOTS): inventory(GCROOTS, (RETAINED,)), str(ENABLE): inventory(ENABLE),
        'retained_root_directories': {RETAINED: retained},
        'captured_utc': datetime.now(timezone.utc).isoformat(),
    }
    require(os.readlink(CURRENT) == PROFILE, 'Host profile changed during capture')
    os.mkdir(BACKUP, 0o700)
    fsync_dir(BACKUP.parent)
    for name, data in contents.items():
        write_new(BACKUP / name, data)
    data = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    write_new(BACKUP / 'rollback-manifest.json.tmp', data)
    os.replace(BACKUP / 'rollback-manifest.json.tmp', BACKUP / 'rollback-manifest.json')
    fsync_dir(BACKUP)
    print(f'ROLLBACK_CAPTURED {GENERATION} sha256={hashlib.sha256(data).hexdigest()} units=4 '
          f'retained={RETAINED} image={IMAGE_SHA[:12]} no-service-change', flush=True)
    return manifest


if __name__ == '__main__':
    capture()
