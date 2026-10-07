#!/usr/bin/env bash
# The bash -c programs expand their own positional parameters.
# In-container bootstrap of one hound-ci job (container.nix's hound-ci-job.service).
# Markers on the console are advisory to the host, like the VMs' serial markers.
set -euo pipefail
runner_package=$1
runner_env=$2
dev_env=$3

fail() {
  echo "HOUND_CI_GUEST_PREFLIGHT_FAILED $*"
  exit 1
}

jit_file="${CREDENTIALS_DIRECTORY:-}/jit"
[[ -r $jit_file ]] || fail 'no jit credential'

# The host configures host0 once the container runs (supervisor job-network): wait
# for its default route (event-driven, bounded), never for host DNS.
# The monitor starts before the second check, so a route added in between is seen.
if ! ip -4 route show default | grep -q .; then
  coproc route_monitor { exec timeout 120 ip monitor route; }
  if ! ip -4 route show default | grep -q .; then
    grep -m1 -q '^default' <&"${route_monitor[0]}" || true
  fi
  # shellcheck disable=SC2154 # coproc sets route_monitor_PID
  kill "$route_monitor_PID" 2>/dev/null || true
fi
ip -4 route show default | grep -q . || fail 'no network'

# The runner's environment starts empty: only runner.env and dev.env.
as_runner() {
  runuser -u runner -- env -i bash -c 'set -eu; . "$0"; . "$1"; shift; exec "$@"' \
    "$runner_env" "$dev_env" "$@"
}

# Preflight: Docker (the job's own daemon), Chrome's namespace sandbox (no
# --no-sandbox), and that host/private networks stay unreachable (the host's nft
# table's job chains drop them; this checks it from the inside).
as_runner docker info --format 'Docker {{.ServerVersion}} storage={{.Driver}}' || fail docker
as_runner /usr/bin/google-chrome --version || fail chrome
profile=$(as_runner mktemp -d)
timeout --kill-after=5s 60s runuser -u runner -- env -i bash -c '. "$0"; exec "$@"' "$runner_env" \
  /usr/bin/google-chrome --headless --disable-gpu --disable-dev-shm-usage --no-first-run \
  --user-data-dir="$profile" --dump-dom 'data:text/html,<body>HOUND_CI_CHROME_SANDBOX_OK</body>' \
  2>/dev/null | grep -q HOUND_CI_CHROME_SANDBOX_OK || fail 'chrome sandbox'
rm -rf -- "$profile"
python3 - <<'PY' || fail 'host or private network reachable'
import socket
for host in ('10.0.2.2', '192.168.1.150', '100.77.9.102', '169.254.169.254', '172.17.0.1'):
    with socket.socket() as stream:
        stream.settimeout(2)
        try:
            stream.connect((host, 22))
        except OSError:
            continue
        raise SystemExit(f'{host} reachable')
PY
# Positive egress: public DNS and HTTPS to GitHub, as the runner needs.
as_runner curl --fail --silent --show-error --max-time 30 --output /dev/null https://github.com/ \
  || fail 'no egress to github.com'
echo 'HOUND_CI_GUEST_PREFLIGHT_OK'

install -d -o runner -g users -m 0700 /home/runner/actions-runner
jit=$(cat "$jit_file")
# RUNNER_ROOT holds the runner's writable state (nixpkgs' github-runner reads it).
# The JIT config is single-use and in this container's /proc only.
set +e
runuser -u runner -- env -i bash -c 'set -eu; . "$0"; . "$1"; shift; exec "$@"' \
  "$runner_env" "$dev_env" env RUNNER_ROOT=/home/runner/actions-runner \
  "$runner_package/bin/Runner.Listener" run --jitconfig "$jit"
status=$?
set -e
unset jit
echo "HOUND_CI_GUEST_JOB_FINISHED status=$status"
