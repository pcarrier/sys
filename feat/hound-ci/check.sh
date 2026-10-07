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
"${PYTHON:-python3}" -I -B feat/hound-ci/test_capture_rollback.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_generation.py
"${PYTHON:-python3}" -I -B feat/hound-ci/test_deploy.py
bash -n feat/hound-ci/guest.sh
bash -n feat/hound-ci/provision.sh
bash -n feat/hound-ci/cache-only.sh
bash -n feat/hound-ci/container-start.sh
git diff --check
git diff --cached --check
nix eval --json .#nixosConfigurations.hound.config.services.hound-ci
# Slot labels: hound's reservation, the default 0 (no --labels) and 2 slots.
nix eval --json --impure --expr "
  let
    hound = (builtins.getFlake \"git+file://$PWD\").nixosConfigurations.hound;
    reserve = n: hound.extendModules { modules = [ { services.hound-ci.reservedMainSlots = hound.pkgs.lib.mkForce n; } ]; };
    exec = c: map (n: c.config.systemd.services.\"hound-ci-\${toString n}\".serviceConfig.ExecStart) [ 1 2 3 4 ];
  in { hound = exec hound; zero = exec (reserve 0); two = exec (reserve 2); }" |
  "${PYTHON:-python3}" -I -B feat/hound-ci/check_slot_labels.py
nix build --no-link --print-out-paths \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-storage.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-firewall.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-1.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-2.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-3.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-4.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci.slice".unit'
# deploy-nspawn.py's pinned new units are exactly what this tree builds.
nix build --no-link --print-out-paths \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-1.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-2.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-3.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-4.service".unit' \
  '.#nixosConfigurations.hound.config.systemd.units."hound-ci-firewall.service".unit' |
  "${PYTHON:-python3}" -I -B -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("deploy", "feat/hound-ci/deploy-nspawn.py")
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
built = sys.stdin.read().split()
assert sorted(built) == sorted(deploy.NEW.values()), ("pinned NEW units differ from the build", built)
print("PINNED_UNITS_OK")'
