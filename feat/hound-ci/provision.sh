#!/usr/bin/env bash
# TRUSTED GOLDEN-IMAGE BUILD ONLY: root, Ubuntu 24.04 amd64, disposable VM.
# Never invoke this on hound or on an already registered job worker. The root
# supervisor owns the pinned official Ubuntu base image and its verification.
# This file neither registers a runner nor receives any GitHub credentials.
#
# Caller contract:
#   * guest worker services use EnvironmentFile=/etc/hound-ci/runner.env;
#   * wait for HOUND_CI_PROVISION_OK on the serial console AND successful
#     cloud-final.service completion before sealing the image;
#   * after cloud-init finishes, clean with `cloud-init clean --logs --seed`
#     and power off in a separate shutdown unit. Do NOT power off/clean here or
#     wait for cloud-init here: this script itself runs inside cloud-final.
# No registration state or signing identities are baked in. Public pinned
# Docker images are seeded by the separate, bounded trusted cache builder.
set -Eeuo pipefail
umask 022

[[ ${EUID} -eq 0 ]] || { echo 'Provisioning requires guest root.' >&2; exit 1; }
# Guard before any mutation: the host is not this OS, and containers are not VMs.
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 ]] || {
  echo 'Only the trusted Ubuntu 24.04 guest may be provisioned.' >&2; exit 1;
}
[[ $(dpkg --print-architecture) == amd64 ]] || {
  echo 'The guest must be amd64.' >&2; exit 1;
}
systemd-detect-virt --quiet --vm || {
  echo 'Provisioning is restricted to a virtual machine.' >&2; exit 1;
}
[[ -c /dev/ttyS0 ]] || { echo 'Serial console /dev/ttyS0 is required.' >&2; exit 1; }
[[ ! -e /opt/actions-runner/.runner && ! -e /opt/actions-runner/.credentials && ! -e /opt/actions-runner/.credentials_rsaparams ]] || {
  echo 'Refusing to provision an already registered runner guest.' >&2; exit 1;
}
# Keep an independent guest-file witness; the wrapper reports its real exit
# and bounded failure excerpt after the child exits. Serial/getty is not the log.
exec >>/var/log/hound-ci-provision.log 2>&1
provision_failed() {
  local rc=$?
  printf 'HOUND_CI_PROVISION_FAILED line=%s status=%s\n' "$1" "$rc"
  exit "$rc"
}
trap 'provision_failed "$LINENO"' ERR
printf 'HOUND_CI_PROVISION_START utc=%s\n' "$(date -u +%FT%TZ)"

readonly HELM_VERSION=v3.19.0
readonly RUST_VERSION=1.99.0
readonly RUNNER_VERSION=2.337.0
readonly RUNNER_SHA256=70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613
readonly RUNNER_HOME=/home/runner
readonly WORKER_PATH=/home/runner/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

scratch=$(mktemp -d /tmp/hound-ci-provision.XXXXXXXX)
readonly scratch
# The verified rustup-init executable must be traversable by the runner user.
chmod 0755 "$scratch"
trap 'rm -rf -- "$scratch"' EXIT

# One bounded request per artifact; failures abort rather than silently
# selecting another release or retrying a partial/image build.
fetch() {
  local url=$1 destination=$2
  curl --fail --silent --show-error --location --proto '=https' \
    --proto-redir '=https' --tlsv1.2 --connect-timeout 20 --max-time 600 \
    --output "$destination" "$url"
}
verify_sha256() {
  local expected=$1 archive=$2
  [[ $expected =~ ^[[:xdigit:]]{64}$ ]] || {
    echo 'Invalid upstream SHA-256 manifest.' >&2; return 1;
  }
  printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --strict -
}
as_runner() {
  runuser -u runner -- env -i \
    HOME="$RUNNER_HOME" USER=runner LOGNAME=runner LANG=C.UTF-8 \
    CARGO_HOME="$RUNNER_HOME/.cargo" RUSTUP_HOME="$RUNNER_HOME/.rustup" \
    PATH="$WORKER_PATH" "$@"
}

apt-get update
apt_packages=(
  build-essential cmake pkg-config clang libclang-dev libssl-dev
  libvulkan1 mesa-vulkan-drivers mkcert fonts-dejavu-core dbus-daemon
  curl unzip git jq python3 openssl ca-certificates libicu74 docker.io
  gnupg sudo xz-utils util-linux zlib1g libkrb5-3
  python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 xvfb xauth
)
apt-get install -y --no-install-recommends "${apt_packages[@]}"
printf 'HOUND_CI_STAGE native-apt-complete\n'

install -d -m 0755 /etc/apt/keyrings
printf 'HOUND_CI_STAGE google-key-fetch\n'
# Official Google APT repository, scoped signing key (never apt-key).
fetch https://dl.google.com/linux/linux_signing_key.pub "$scratch/google.asc"
gpg --batch --yes --dearmor --output /etc/apt/keyrings/google-chrome.gpg "$scratch/google.asc"
cat >/etc/apt/sources.list.d/google-chrome.list <<'APT'
deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main
APT
# Official Microsoft Ubuntu 24.04 repository for PowerShell.
fetch https://packages.microsoft.com/keys/microsoft.asc "$scratch/microsoft.asc"
gpg --batch --yes --dearmor --output /etc/apt/keyrings/microsoft.gpg "$scratch/microsoft.asc"
cat >/etc/apt/sources.list.d/microsoft-prod.list <<'APT'
deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main
APT
# GitHub CLI's official signed APT repository.
fetch https://cli.github.com/packages/githubcli-archive-keyring.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg
cat >/etc/apt/sources.list.d/github-cli.list <<'APT'
deb [arch=amd64 signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main
APT
chmod 0644 /etc/apt/keyrings/google-chrome.gpg /etc/apt/keyrings/microsoft.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg
apt-get update
apt-get install -y --no-install-recommends google-chrome-stable powershell gh
# Chrome's post-install may create its own source entry. Keep exactly one
# signed-by-scoped Google source after the package has completed installation.
cat >/etc/apt/sources.list.d/google-chrome.list <<'APT'
deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main
APT
[[ -x /usr/bin/google-chrome ]]

if ! id runner >/dev/null 2>&1; then
  useradd --create-home --home-dir "$RUNNER_HOME" --shell /bin/bash runner
fi
[[ $(id -u runner) -ne 0 ]]
[[ $(getent passwd runner | cut -d: -f6) == "$RUNNER_HOME" ]]
passwd --lock runner
usermod --append --groups docker runner
install -d -o runner -g runner -m 0750 "$RUNNER_HOME"
# Root inside this disposable, job-owned guest is intentionally allowed. This
# sudo policy never affects the supervisor/host and contains no host secrets.
printf 'runner ALL=(ALL:ALL) NOPASSWD: ALL\n' >/etc/sudoers.d/90-hound-ci-runner
chmod 0440 /etc/sudoers.d/90-hound-ci-runner
visudo --check --file /etc/sudoers.d/90-hound-ci-runner

install -d -m 0755 /etc/hound-ci
cat >/etc/profile.d/hound-ci.sh <<'PROFILE'
# For interactive/login shells of the guest runner only.
if [ "${USER:-}" = runner ]; then
  export HOME=/home/runner
  export CARGO_HOME=/home/runner/.cargo
  export RUSTUP_HOME=/home/runner/.rustup
  export PATH=/home/runner/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
fi
PROFILE
cat >/etc/hound-ci/runner.env <<'WORKER_ENV'
HOME=/home/runner
USER=runner
LOGNAME=runner
CARGO_HOME=/home/runner/.cargo
RUSTUP_HOME=/home/runner/.rustup
PATH=/home/runner/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
LANG=C.UTF-8
RUNNER_TOOL_CACHE=/opt/hostedtoolcache
WORKER_ENV
chmod 0644 /etc/profile.d/hound-ci.sh /etc/hound-ci/runner.env

# Exact official Node 24 AND 26 pins; Actions-compatible layout and sentinels.
# The controller supplies only this audited source and data-only bounded pins.
python3 /root/cache-ci.py nodes --pins /root/cache-pins.json
NODE_VERSION=v24.21.0
NODE_SHA256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
readonly NODE_VERSION NODE_SHA256

# Pinned Helm v3 release, verified against the official release checksum.
helm_archive="helm-${HELM_VERSION}-linux-amd64.tar.gz"
fetch "https://get.helm.sh/${helm_archive}.sha256sum" "$scratch/helm-sha256sum"
HELM_SHA256=$(awk 'NR == 1 { print $1 }' "$scratch/helm-sha256sum")
readonly HELM_SHA256
fetch "https://get.helm.sh/${helm_archive}" "$scratch/$helm_archive"
verify_sha256 "$HELM_SHA256" "$scratch/$helm_archive"
tar --extract --gzip --file "$scratch/$helm_archive" --directory "$scratch" \
  --no-same-owner linux-amd64/helm
install -m 0755 "$scratch/linux-amd64/helm" /usr/local/bin/helm

# Official rustup binary plus its upstream checksum; never curl | sh. The
# bootstrap may evolve, but the CI compiler/toolchain is EXACTLY 1.99.0. If
# that release is unavailable upstream, fail: do not substitute another one.
fetch https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init.sha256 "$scratch/rustup-init.sha256"
RUSTUP_SHA256=$(awk 'NR == 1 { print $1 }' "$scratch/rustup-init.sha256")
readonly RUSTUP_SHA256
fetch https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init "$scratch/rustup-init"
verify_sha256 "$RUSTUP_SHA256" "$scratch/rustup-init"
chmod 0755 "$scratch/rustup-init"
as_runner "$scratch/rustup-init" --yes --no-modify-path --profile minimal \
  --default-toolchain "$RUST_VERSION" --component rustfmt --component clippy

# Runner payload only: no config.sh, JIT config, registration token or service.
runner_archive="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
fetch "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${runner_archive}" "$scratch/$runner_archive"
verify_sha256 "$RUNNER_SHA256" "$scratch/$runner_archive"
install -d -o runner -g runner -m 0755 /opt/actions-runner
tar --extract --gzip --file "$scratch/$runner_archive" --directory /opt/actions-runner --no-same-owner
chown -R runner:runner /opt/actions-runner

# Ubuntu's AppArmor userns restriction otherwise blocks Chrome's namespace
# sandbox. This setting is permanent INSIDE this disposable VM, not on hound.
printf 'kernel.apparmor_restrict_unprivileged_userns = 0\n' >/etc/sysctl.d/90-hound-ci-chrome.conf
sysctl --load /etc/sysctl.d/90-hound-ci-chrome.conf
systemctl enable --now docker.service

# Preflight runs with the SAME explicit home/path/user/group policy as jobs.
as_runner python3 --version
# PowerShell, not the provisioning shell, expands this variable.
# shellcheck disable=SC2016
as_runner pwsh -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()'
helm_actual=$(as_runner helm version --short)
[[ $helm_actual == "$HELM_VERSION"+* || $helm_actual == "$HELM_VERSION" ]]
printf '%s\n' "$helm_actual"
as_runner gh --version
as_runner /usr/bin/google-chrome --version
[[ $(as_runner node --version) == "$NODE_VERSION" ]]
as_runner npm --version
rust_actual=$(as_runner rustc --version)
[[ $rust_actual == "rustc ${RUST_VERSION} "* ]]
printf '%s\n' "$rust_actual"
as_runner cargo --version
as_runner rustfmt --version
as_runner cargo clippy --version
[[ $(as_runner /opt/actions-runner/bin/Runner.Listener --version) == "$RUNNER_VERSION" ]]
# Tests daemon readiness AND the unprivileged runner's docker-group access.
# No hello-world pull: avoid baking an unnecessary image into every VM clone.
as_runner docker info --format 'Docker server {{.ServerVersion}} storage={{.Driver}}'
as_runner sudo --non-interactive true
as_runner unshare --user --map-root-user --pid --fork true
# Native WebKit tests need the hosted-image GI/GTK/WebKit and X11 prerequisites.
as_runner xvfb-run --auto-servernum /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2; Gtk.init([]); view=WebKit2.WebView(); print("WebKit2 GTK/Xvfb preflight OK")'

chrome_profile=$(as_runner mktemp -d /home/runner/.hound-ci-chrome.XXXXXXXX)
# Disable only the setuid fallback to require the real namespace sandbox.
# All Chrome sandboxing remains enabled; the browser runs as runner, not root.
timeout --signal=TERM --kill-after=5s 60s runuser -u runner -- env -i \
  HOME="$RUNNER_HOME" USER=runner LOGNAME=runner LANG=C.UTF-8 PATH="$WORKER_PATH" \
  /usr/bin/google-chrome --headless --disable-setuid-sandbox \
  --disable-gpu --disable-dev-shm-usage --no-first-run --no-default-browser-check \
  --user-data-dir="$chrome_profile" --dump-dom \
  'data:text/html,<html><body>HOUND_CI_CHROME_SANDBOX_OK</body></html>' \
  >"$scratch/chrome-dom.html"
grep --fixed-strings --quiet '<body>HOUND_CI_CHROME_SANDBOX_OK</body>' "$scratch/chrome-dom.html"
rm -rf -- "$chrome_profile"

# Full private Docker seeds and provenance, built only from exact trusted main.
# This also performs an offline preflight and stops Docker/socket/containerd.
install -m 0755 /root/cache-ci.py /opt/hound-ci-cache.py
install -m 0644 /root/cache-pins.json /etc/hound-ci/cache-pins.json
python3 /opt/hound-ci-cache.py build --pins /etc/hound-ci/cache-pins.json

# Nonsecret provenance for this sealed image; apt packages intentionally follow
# their official signed repositories at build time and are recorded here.
{
  printf 'BUILD_UTC=%s\n' "$(date -u +%FT%TZ)"
  printf 'NODE_VERSION=%s\nNODE_SHA256=%s\n' "$NODE_VERSION" "$NODE_SHA256"
  printf 'HELM_VERSION=%s\nHELM_SHA256=%s\n' "$HELM_VERSION" "$HELM_SHA256"
  printf 'RUST_VERSION=%s\nRUSTUP_SHA256=%s\n' "$RUST_VERSION" "$RUSTUP_SHA256"
  printf 'RUNNER_VERSION=%s\nRUNNER_SHA256=%s\n' "$RUNNER_VERSION" "$RUNNER_SHA256"
  dpkg-query --show --showformat='${binary:Package}\t${Version}\n' \
    "${apt_packages[@]}" google-chrome-stable powershell gh
} >/etc/hound-ci/image-versions
chmod 0644 /etc/hound-ci/image-versions
apt-get clean
rm -rf -- /var/lib/apt/lists/*
# Remove downloaded archives before reporting success. cloud-init cleanup and
# poweroff are deliberately owned by the caller after cloud-final completes.
rm -rf -- "$scratch"
trap - EXIT
printf 'HOUND_CI_PROVISION_OK\n'
