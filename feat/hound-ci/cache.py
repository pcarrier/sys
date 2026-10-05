#!/usr/bin/env python3
"""Trusted image builder/preflight. Never harvest data from job overlays.

Only fixed public pins enter the seed. No host mount, cache server, npm config,
Cargo target, user credential, or registration is accepted by this program.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

TOOLCACHE = Path('/opt/hostedtoolcache')
MANIFEST = Path('/etc/hound-ci/cache-manifest.json')
RECIPE_LABEL = 'app.ultimator.ci.recipe-sha256'
CONTRACT_LABEL = 'app.ultimator.ci.recipe-contract'
NATIVE_PACKAGES = (
    'build-essential', 'cmake', 'pkg-config', 'clang', 'libclang-dev', 'libssl-dev',
    'libvulkan1', 'mesa-vulkan-drivers', 'mkcert', 'fonts-dejavu-core', 'dbus-daemon',
    'libc6', 'libstdc++6', 'zlib1g', 'python3-gi', 'gir1.2-gtk-3.0',
    'gir1.2-webkit2-4.1', 'xvfb', 'xauth',
)
BASE_TAGS = frozenset(('debian:bookworm-slim', 'alpine:latest', 'node:24',
                       'docker:dind', 'ubuntu:24.04', 'postgres:17',
                       'clickhouse/clickhouse-server:26.8'))
HEX256 = re.compile(r'[0-9a-f]{64}\Z')
VERSION = re.compile(r'(24|26)\.[0-9]{1,3}\.[0-9]{1,3}\Z')
MAX_IMAGE_BYTES = 16 * 1024 ** 3


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=1200, **kwargs)


def output(argv):
    return command(argv, stdout=subprocess.PIPE, text=True).stdout.strip()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_json(path, limit=65536):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('JSON exceeds format bound')
    # Duplicate keys must not silently change a pin or ledger field.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs)


def validate_pins(pins):
    if type(pins.get('schema')) is not int or pins.get('schema') != 1 or set(pins) != {'schema', 'source_commit', 'nodes', 'images', 'fixtures'}:
        raise ValueError('Unsupported seed schema')
    if not re.fullmatch(r'[0-9a-f]{40}', pins['source_commit']):
        raise ValueError('Source must be an exact trusted main commit')
    if len(pins['nodes']) != 2 or {n['version'].split('.')[0] for n in pins['nodes']} != {'24', '26'}:
        raise ValueError('Exactly Node 24 and 26 are required')
    for node in pins['nodes']:
        if set(node) != {'version', 'sha256'} or not VERSION.fullmatch(node['version']) or not HEX256.fullmatch(node['sha256']):
            raise ValueError('Invalid Node pin')
    if len(pins['images']) != len(BASE_TAGS) or {i['tag'] for i in pins['images']} != BASE_TAGS:
        raise ValueError('Image allowlist mismatch')
    for image in pins['images']:
        if set(image) != {'tag', 'digest', 'manifest_sha256', 'compressed_bytes'}:
            raise ValueError('Invalid image pin fields')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image['digest']) or not HEX256.fullmatch(image['manifest_sha256']):
            raise ValueError('Invalid manifest digest')
        if type(image['compressed_bytes']) is not int or not 0 < image['compressed_bytes'] < 4 * 1024 ** 3:
            raise ValueError('Compressed image budget exceeded')
    if len(pins['fixtures']) != 2 or {f['name'] for f in pins['fixtures']} != {'browser', 'yas'}:
        raise ValueError('Fixture allowlist mismatch')
    for fixture in pins['fixtures']:
        if set(fixture) != {'name', 'sha256'} or not HEX256.fullmatch(fixture['sha256']):
            raise ValueError('Invalid fixture pin')
    return pins


def fetch(url, destination, expected, limit):
    if not url.startswith(('https://nodejs.org/dist/', 'https://raw.githubusercontent.com/xmit-dev/ultimator/')):
        raise ValueError('Artifact origin refused')
    command(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
             '--proto-redir', '=https', '--tlsv1.2', '--connect-timeout', '20', '--max-time', '600',
             '--max-filesize', str(limit), '--output', str(destination), url])
    if not 0 < destination.stat().st_size <= limit or digest(destination) != expected:
        raise ValueError('Artifact length/checksum mismatch')


def extract_node(archive, destination, version):
    """Reject traversal, links out, special files, oversized/duplicate members."""
    root = f'node-v{version}-linux-x64'
    with tarfile.open(archive, mode='r:xz') as tar:
        members = []
        expanded = 0
        seen = set()
        for member in tar:
            expanded += member.size
            if len(members) >= 20000 or expanded > 512 * 1024 ** 2:
                raise ValueError('Node archive format budget exceeded')
            members.append(member)
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != root or '..' in parts or member.name in seen:
                raise ValueError('Invalid/duplicate Node archive path')
            seen.add(member.name)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError('Special Node archive member refused')
            if member.size < 0 or member.size > 256 * 1024 ** 2:
                raise ValueError('Node member length refused')
        # Python data filter independently rejects escaping hard/symlinks and
        # removes special/setuid permissions. Extraction goes into a new stage.
        tar.extractall(destination, members=members, filter='data')
    return destination / root


def install_nodes(pins, scratch):
    ledger = []
    # A job may request an unseeded version. The action needs a writable parent
    # for its normal fallback download inside this PRIVATE disposable VM.
    for parent in (TOOLCACHE, TOOLCACHE / 'node'):
        parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.chown(parent, user='runner', group='runner')
    for node in pins['nodes']:
        version = node['version']
        folder = TOOLCACHE / 'node' / version
        marker = folder / 'x64.complete'
        binary = folder / 'x64/bin/node'
        if folder.exists():
            if not marker.is_file() or output([str(binary), '--version']) != 'v' + version:
                raise ValueError('Existing incomplete/mismatched toolcache refused')
        else:
            archive = scratch / f'node-v{version}-linux-x64.tar.xz'
            fetch(f'https://nodejs.org/dist/v{version}/{archive.name}', archive, node['sha256'], 96 * 1024 ** 2)
            stage = scratch / f'node-{version}'
            stage.mkdir()
            extracted = extract_node(archive, stage, version)
            folder.mkdir(parents=True, mode=0o755)
            shutil.move(str(extracted), folder / 'x64')
            if output([str(binary), '--version']) != 'v' + version:
                raise ValueError('Installed Node version mismatch')
            # setup-node/tool-cache requires exactly this sibling sentinel.
            marker.touch(mode=0o644)
            archive.unlink()
        npm = output([str(binary), str(folder / 'x64/lib/node_modules/npm/bin/npm-cli.js'), '--version'])
        ledger.append({**node, 'npm': npm, 'node_binary_sha256': digest(binary)})
    default = next(node for node in pins['nodes'] if node['version'].startswith('24.'))
    for name in ('node', 'npm', 'npx'):
        link = Path('/usr/local/bin') / name
        link.unlink(missing_ok=True)
        link.symlink_to(TOOLCACHE / 'node' / default['version'] / 'x64/bin' / name)
    return ledger


def native_ledger():
    text = output(['dpkg-query', '--show', '--showformat=${binary:Package}\t${Status}\t${Version}\n', *NATIVE_PACKAGES])
    ledger = {}
    for line in text.splitlines():
        name, status, version = line.split('\t')
        name = name.removesuffix(':amd64')
        if status != 'install ok installed' or name not in NATIVE_PACKAGES or name in ledger:
            raise ValueError('Native prerequisite status mismatch')
        ledger[name] = version
    if set(ledger) != set(NATIVE_PACKAGES):
        raise ValueError('Missing native prerequisite')
    return ledger


def inspect_image(tag):
    value = json.loads(output(['docker', 'image', 'inspect', tag]))
    if len(value) != 1:
        raise ValueError('Expected one Docker image')
    image = value[0]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']) or image['Architecture'] != 'amd64' or image['Os'] != 'linux':
        raise ValueError('Docker image platform/ID mismatch')
    if type(image['Size']) is not int or not 0 < image['Size'] <= MAX_IMAGE_BYTES:
        raise ValueError('Docker image size bound refused')
    return image


def validate_recipe(data):
    text = data.decode('ascii')
    # Contract 1 accepts just the current context-free recipes, not COPY/ADD,
    # external contexts, stages, arbitrary frontend syntax, or local artifacts.
    instructions = [line for line in text.splitlines() if line and not line.startswith('#')]
    if not instructions or instructions[0] not in ('FROM debian:bookworm-slim', 'FROM ubuntu:24.04'):
        raise ValueError('Fixture base contract mismatch')
    if any(re.match(r'(?i)^\s*(COPY|ADD|ARG|ONBUILD|FROM)\b', line) for line in instructions[1:]):
        raise ValueError('Fixture context/stage contract refused')
    if any(line.startswith('# syntax=') for line in text.splitlines()):
        raise ValueError('External Docker frontend refused')


def recipe_bytes(pins, source):
    recipes = {}
    for pin in pins['fixtures']:
        recipe = source / (pin['name'] + '.Dockerfile')
        if not recipe.is_file() or recipe.is_symlink() or not 0 < recipe.stat().st_size <= 16384:
            raise ValueError('Fixture snapshot format bound refused')
        data = recipe.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise ValueError('Trusted main fixture snapshot checksum mismatch')
        validate_recipe(data)
        recipes[pin['name']] = data
    return recipes


def registry_metadata(pin):
    # Anonymous PUBLIC Docker Hub access only; no auth file or controller secret.
    repository = pin['tag'].rsplit(':', 1)[0]
    if '/' not in repository:
        repository = 'library/' + repository
    def request(url, headers=()):
        data = command(['curl', '--fail', '--silent', '--show-error', '--proto', '=https',
                        '--connect-timeout', '20', '--max-time', '120', '--max-filesize', '2097152',
                        *headers, url], stdout=subprocess.PIPE).stdout
        if not 0 < len(data) <= 2 * 1024 ** 2:
            raise ValueError('Registry response format bound exceeded')
        return data
    auth = json.loads(request('https://auth.docker.io/token?service=registry.docker.io&scope=repository:' + repository + ':pull'))
    token = auth['token']
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,16384}', token):
        raise ValueError('Anonymous registry token format refused')
    headers = ('-H', 'Authorization: Bearer ' + token, '-H',
               'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json')
    base = 'https://registry-1.docker.io/v2/' + repository + '/manifests/'
    index_bytes = request(base + 'sha256:' + pin['manifest_sha256'], headers)
    if hashlib.sha256(index_bytes).hexdigest() != pin['manifest_sha256']:
        raise ValueError('Registry index checksum mismatch')
    index = json.loads(index_bytes)
    if index.get('schemaVersion') != 2 or not 0 < len(index.get('manifests', [])) <= 100:
        raise ValueError('Registry index format refused')
    platforms = [item for item in index['manifests'] if item.get('platform', {}).get('os') == 'linux'
                 and item.get('platform', {}).get('architecture') == 'amd64' and not item.get('platform', {}).get('variant')]
    if len(platforms) != 1 or platforms[0]['digest'] != pin['digest']:
        raise ValueError('Registry amd64 manifest mismatch')
    child_bytes = request(base + pin['digest'], headers)
    if 'sha256:' + hashlib.sha256(child_bytes).hexdigest() != pin['digest']:
        raise ValueError('Registry image manifest checksum mismatch')
    child = json.loads(child_bytes)
    layers = child.get('layers', [])
    if child.get('schemaVersion') != 2 or not 0 < len(layers) <= 100:
        raise ValueError('Registry layers format refused')
    if any(type(layer.get('size')) is not int or not 0 < layer['size'] <= 2 * 1024 ** 3
           or not re.fullmatch(r'sha256:[0-9a-f]{64}', layer.get('digest', '')) for layer in layers):
        raise ValueError('Registry layer bounds refused')
    if sum(layer['size'] for layer in layers) != pin['compressed_bytes']:
        raise ValueError('Registry compressed byte attestation mismatch')


def install_images(pins, scratch, recipes):
    images = []
    for pin in pins['images']:
        registry_metadata(pin)
        reference = pin['tag'].rsplit(':', 1)[0] + '@' + pin['digest']
        command(['docker', 'pull', '--platform=linux/amd64', reference])
        command(['docker', 'tag', reference, pin['tag']])
        image = inspect_image(pin['tag'])
        if not any(entry.endswith('@' + pin['digest']) for entry in image.get('RepoDigests', [])):
            raise ValueError('Docker content pin missing')
        images.append({**pin, 'id': image['Id'], 'bytes': image['Size']})
    context = scratch / 'fixtures'
    context.mkdir()
    for pin in pins['fixtures']:
        filename = pin['name'] + '.Dockerfile'
        recipe = context / filename
        recipe.write_bytes(recipes[pin['name']])
    fixtures = []
    for pin in pins['fixtures']:
        tag = 'ultimator-' + pin['name'] + '-test'
        # Warm refresh is allowed only in this credential-free TRUSTED builder,
        # whose source golden file was independently hash-attested by the host.
        # No guest/job layer is ever collected for a future image.
        existing = subprocess.run(['docker', 'image', 'inspect', tag], check=False,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        cached = json.loads(existing.stdout)[0] if existing.returncode == 0 else None
        labels = (cached.get('Config', {}).get('Labels') or {}) if cached else {}
        if labels.get(RECIPE_LABEL) != pin['sha256'] or labels.get(CONTRACT_LABEL) != '1':
            command(['docker', 'build', '--pull=false', '--tag', tag, '--file', str(context / (pin['name'] + '.Dockerfile')),
                     '--label', RECIPE_LABEL + '=' + pin['sha256'], '--label', CONTRACT_LABEL + '=1', str(context)])
        image = inspect_image(tag)
        labels = image['Config']['Labels']
        if labels.get(RECIPE_LABEL) != pin['sha256'] or labels.get(CONTRACT_LABEL) != '1':
            raise ValueError('Complete fixture labels missing')
        fixtures.append({**pin, 'tag': tag, 'id': image['Id'], 'bytes': image['Size']})
    unique = {image['id']: image['bytes'] for image in images + fixtures}
    if sum(unique.values()) > MAX_IMAGE_BYTES:
        raise ValueError('Docker seed exceeds 16 GiB unshared upper-bound budget')
    # Delete intermediate build cache, not any promoted job state. Final images
    # and their complete layers remain in this trusted builder's private Docker.
    command(['docker', 'builder', 'prune', '--all', '--force'])
    return images, fixtures


def guard_guest():
    if os.geteuid() != 0:
        raise ValueError('Builder requires disposable guest root')
    os_release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu\n' not in os_release or 'VERSION_ID="24.04"\n' not in os_release:
        raise ValueError('Only Ubuntu 24.04 may seed an image')
    command(['systemd-detect-virt', '--quiet', '--vm'])
    for path in ('/opt/actions-runner/.runner', '/opt/actions-runner/.credentials',
                 '/opt/actions-runner/.credentials_rsaparams', '/etc/hound-ci-registration.json',
                 '/root/.docker/config.json', '/home/runner/.docker/config.json',
                 '/home/runner/.config/gh/hosts.yml', '/root/.config/gh/hosts.yml'):
        if Path(path).exists():
            raise ValueError('Credential/registration-bearing image refused')


def build(pins, pins_path, nodes_only=False):
    guard_guest()
    # Fail all private-source format/hash checks BEFORE any downloads. These two
    # data-only snapshots were independently fetched from trusted upstream main
    # by the operator; the guest receives NO GitHub/CLI credentials.
    recipes = recipe_bytes(pins, Path('/root/ci-fixtures'))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='hound-ci-cache-') as scratch:
        nodes = install_nodes(pins, Path(scratch))
        if nodes_only:
            return
        command(['systemctl', 'start', 'docker.service'])
        images, fixtures = install_images(pins, Path(scratch), recipes)
        manifest = {'schema': 1, 'contract': 1, 'source_commit': pins['source_commit'],
                    'pins_sha256': digest(pins_path), 'builder_sha256': digest(__file__), 'nodes': nodes, 'native_packages': native_ledger(),
                    'images': images, 'fixtures': fixtures, 'seed_seconds': round(time.monotonic() - started, 3)}
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        MANIFEST.chmod(0o644)
        preflight(pins, pins_path)
        command(['systemctl', 'stop', 'docker.service', 'docker.socket', 'containerd.service'])
        for service in ('docker.service', 'docker.socket', 'containerd.service'):
            state = subprocess.run(['systemctl', 'is-active', service], check=False, stdout=subprocess.PIPE, text=True).stdout.strip()
            if state not in ('inactive', 'failed'):
                raise ValueError('Docker/containerd must be stopped before sealing')
        print('HOUND_CI_CACHE_BUILD_OK ' + json.dumps(manifest, sort_keys=True), flush=True)


def preflight(pins, pins_path):
    manifest = load_json(MANIFEST)
    if manifest.get('schema') != 1 or manifest.get('contract') != 1 or manifest.get('pins_sha256') != digest(pins_path) or manifest.get('builder_sha256') != digest(__file__):
        raise ValueError('Image cache ledger contract/pin mismatch')
    if manifest['source_commit'] != pins['source_commit'] or manifest['native_packages'] != native_ledger():
        raise ValueError('Native/source ledger mismatch')
    if len(manifest['nodes']) != 2 or len(manifest['images']) != len(BASE_TAGS) or len(manifest['fixtures']) != 2:
        raise ValueError('Cache ledger cardinality mismatch')
    for node, pin in zip(manifest['nodes'], pins['nodes'], strict=True):
        folder = TOOLCACHE / 'node' / pin['version']
        if any(node[key] != pin[key] for key in pin) or not (folder / 'x64.complete').is_file():
            raise ValueError('Node pin/sentinel mismatch')
        if output([str(folder / 'x64/bin/node'), '--version']) != 'v' + pin['version'] or digest(folder / 'x64/bin/node') != node['node_binary_sha256']:
            raise ValueError('Node binary mismatch')
    for seeded, pin in zip(manifest['images'], pins['images'], strict=True):
        if any(seeded[key] != pin[key] for key in pin):
            raise ValueError('Seeded image pin mismatch')
        image = inspect_image(pin['tag'])
        if image['Id'] != seeded['id'] or image['Size'] != seeded['bytes'] or not any(d.endswith('@' + pin['digest']) for d in image.get('RepoDigests', [])):
            raise ValueError('Seeded Docker image mismatch')
    for fixture, pin in zip(manifest['fixtures'], pins['fixtures'], strict=True):
        image = inspect_image('ultimator-' + pin['name'] + '-test')
        if any(fixture[key] != pin[key] for key in pin) or image['Id'] != fixture['id'] or image['Size'] != fixture['bytes']:
            raise ValueError('Fixture ID/pin mismatch')
        labels = image['Config'].get('Labels') or {}
        if labels.get(RECIPE_LABEL) != pin['sha256'] or labels.get(CONTRACT_LABEL) != '1':
            raise ValueError('Fixture recipe label mismatch')
    print('HOUND_CI_CACHE_PREFLIGHT_OK offline=1', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('nodes', 'build', 'preflight'))
    parser.add_argument('--pins', required=True)
    args = parser.parse_args()
    pins = validate_pins(load_json(args.pins))
    if args.mode == 'preflight':
        preflight(pins, args.pins)
    else:
        build(pins, args.pins, nodes_only=args.mode == 'nodes')


if __name__ == '__main__':
    main()
