#!/usr/bin/env bash
# CACHE-ONLY trusted upgrade of the checksum-attested pristine old golden VM.
# Never a job overlay; supervisor fixes source path, format, ownership and hash.
set -Eeuo pipefail
umask 022
# Guard before even copying sources; this command must never mutate hound.
[[ ${EUID} -eq 0 ]]
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 ]]
[[ $(dpkg --print-architecture) == amd64 ]]
systemd-detect-virt --quiet --vm
[[ -c /dev/ttyS0 ]]
[[ ! -e /opt/actions-runner/.runner && ! -e /opt/actions-runner/.credentials && ! -e /opt/actions-runner/.credentials_rsaparams ]]
install -d -m 0755 /etc/hound-ci
install -m 0755 /root/cache-ci.py /opt/hound-ci-cache.py
install -m 0644 /root/cache-pins.json /etc/hound-ci/cache-pins.json
python3 /opt/hound-ci-cache.py build --pins /etc/hound-ci/cache-pins.json
# The actual job bootstrap supplies these explicitly, not just this file.
if ! grep -qx 'RUNNER_TOOL_CACHE=/opt/hostedtoolcache' /etc/hound-ci/runner.env; then
  printf 'RUNNER_TOOL_CACHE=/opt/hostedtoolcache\n' >>/etc/hound-ci/runner.env
fi
printf 'HOUND_CI_PROVISION_OK\n'
