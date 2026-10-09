#!/usr/bin/env bash
# Source-only verification: no accounts, nft tables, VM images or services change.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
"${PYTHON:-python3}" -I -B feat/hound-ci/test_supervisor.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_cache.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_drain.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_wait_drained.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_finish_drained.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_activate.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_anchor_proof.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_effect_proof.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_prepare_rollback.py
bash -n feat/hound-ci/guest.sh
bash -n feat/hound-ci/provision.sh
bash -n feat/hound-ci/cache-only.sh
git diff --check
git diff --cached --check
nix eval --json .#nixosConfigurations.hound.config.services.hound-ci
nix build --no-link --print-out-paths \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-storage.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-firewall.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-image.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-1.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-2.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-3.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-4.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci.slice".unit'
