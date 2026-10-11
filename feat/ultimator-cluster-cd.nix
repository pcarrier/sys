# Continuous deployment of Ultimator to its cluster (i0, i1 and i2, xmit-dev/ultimator's deploy/nixos): each push to
# xmit-dev/ultimator's main goes live through .github/workflows/deploy-main.yml there, whose job runs on this host.
#
# A GitHub Actions runner of xmit-dev's, `<host>-deploy`, labelled `hound` and `deploy` and with none of GitHub's
# default labels (`self-hosted`, `Linux`, `X64`), so only jobs naming both land here, never the org's CI (which
# feat/github-runner.nix's runner takes); in the runner group `deploy`, which only xmit-dev/ultimator may use. It
# runs as its own user, github-deploy, not as the CI runner's github-runner: CI jobs on this host can't start a
# release. That user may do one thing beyond what any user may (polkit, below): start
# ultimator-cluster-release@<run>.service, where <run> is the workflow run's ID and attempt (`dry-` first for a
# dry run).
#
# Each such unit runs as pcarrier (whose Nix and Git reach the private flower and yas inputs, and whose sudo
# installs the sandboxes' yas, as deploy/nixos/release.sh does on the sandbox Docker host):
#   - the hold: while /var/lib/ultimator-cluster-release/hold exists, runs stop, saying why, before anything is
#     fetched (put one there before a release that can't roll, such as a Flower compatibility-contract change,
#     which needs every voter switched at once);
#   - the lock, /var/lib/ultimator-cluster-release/lock, which releases by hand take too
#     (`flock /var/lib/ultimator-cluster-release/lock deploy/nixos/release.sh`), waiting for it up to an hour;
#   - main, fetched into a clean checkout of its own (/var/lib/ultimator-cluster-release/checkout: reset and cleaned
#     each run, never anyone's working tree);
#   - its deploy/nixos/release.sh (--build-only for a dry run), with CLUSTER_KNOWN_HOSTS the hosts' installed keys
#     (/etc/ultimator-cluster/known-hosts, below) and CLUSTER_SSH_KEY /var/lib/ultimator-cluster-release/deploy-key
#     (pcarrier's, 0600: generate it, add its public half to deploy/nixos/inventory.nix's sshAuthorizedKeys and
#     release that), or pcarrier's own ~/.ssh/id_ed25519 until it exists, with a warning.
# Its output goes to /var/log/ultimator-cluster-release/<run>.log, which the job follows; the logs go after 30 days.
# A release goes on to its end when the job stops early (a cancelled run, this runner restarting).
# Forge's deploy executor (feat/forge-deploy.nix) starts the same unit as forge-<run>-<attempt>.
#
# The runner registers with /var/lib/secrets/github-runner-deploy.token: a registration token (org › Settings ›
# Actions › Runners › New runner, or `gh api -X POST orgs/xmit-dev/actions/runners/registration-token --jq .token`;
# good for an hour), written without a trailing newline (`printf %s`). It keeps its own credentials in
# /var/lib/github-runner/<host>-deploy once registered.
{
  config,
  ...
}:
let
  host = config.networking.hostName;
  name = "${host}-deploy";
  user = "pcarrier";
  state = "/var/lib/ultimator-cluster-release";
  logs = "/var/log/ultimator-cluster-release";
  work = "/var/lib/github-deploy-work";
in
{
  users = {
    users.github-deploy = {
      isSystemUser = true;
      group = "github-deploy";
      home = work;
      description = "GitHub Actions runner for Ultimator's cluster releases";
    };
    groups.github-deploy = { };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/secrets 0700 root root -"
    "d ${work} 0750 github-deploy github-deploy -"
    "d ${work}/${name} 0750 github-deploy github-deploy -"
    "d ${state} 0750 ${user} users -"
    "d ${logs} 0755 ${user} users 30d"
  ];

  # The cluster hosts' installed SSH host keys (deploy/nixos/install: each host's /etc/ssh/ssh_host_ed25519_key).
  environment.etc."ultimator-cluster/known-hosts".text = ''
    i0.ultimator.app,5.9.17.236 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEWbZVsgRbeGjyDjEfinUnUVIktJZ99DIbl+gkDxN/t5
    i1.ultimator.app,5.9.17.142 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBVyBfhozao4IYqekcRlR5D9XdHWKPfOmPsAzM3BBpLB
    i2.ultimator.app,5.9.17.82 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO77z4mxVv5cZYkBVX4yJXcXqZMuZ778WBRGD3bR3b08
  '';

  services.github-runners.${name} = {
    enable = true;
    url = "https://github.com/xmit-dev";
    tokenFile = "/var/lib/secrets/github-runner-deploy.token";
    inherit name;
    replace = true;
    # xmit-dev's runner group `deploy`, which only xmit-dev/ultimator's workflows may use.
    runnerGroup = "deploy";
    noDefaultLabels = true;
    extraLabels = [
      host
      "deploy"
    ];
    user = "github-deploy";
    group = "github-deploy";
    workDir = "${work}/${name}";
    serviceOverrides = {
      # Checkouts, the release's state and the CI runner's work: jobs here start a unit and follow its log.
      InaccessiblePaths = [
        "/src"
        "/home"
        state
        "-/var/lib/github-runner-work"
      ];
      CPUWeight = 20;
      IOWeight = 20;
      MemoryHigh = "2G";
      MemoryMax = "4G";
    };
  };

  security.polkit.extraConfig = ''
    // The deploy runner (feat/ultimator-cluster-cd.nix) may start releases of Ultimator's main, and nothing else.
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "github-deploy" &&
          action.lookup("verb") == "start" &&
          /^ultimator-cluster-release@(dry-)?[0-9]+-[0-9]+\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
    });
  '';

  systemd.services."ultimator-cluster-release@" = {
    description = "Release Ultimator's main to its cluster (run %i)";
    # The setuid wrappers first (sudo: /run/current-system/sw has a sudo without the setuid bit), then pcarrier's
    # tools (git, gh as Git's credential helper, ssh's config), then the system's (nix, ssh, jq, flock).
    path = [
      "/run/wrappers"
      "/etc/profiles/per-user/${user}"
      "/run/current-system/sw"
    ];
    # A release under way goes on to its end through a switch of this host.
    restartIfChanged = false;
    stopIfChanged = false;
    serviceConfig = {
      Type = "oneshot";
      User = user;
      Group = "users";
      WorkingDirectory = state;
      StandardOutput = "truncate:${logs}/%i.log";
      StandardError = "inherit";
      # deploy-main.yml gives the job 180 minutes: a build of 20 to 40, a roll of about 15, an hour for the lock.
      TimeoutStartSec = "170min";
    };
    # Each run its own instance: gone once it ends, failed or not (its log stays).
    unitConfig.CollectMode = "inactive-or-failed";
    scriptArgs = "%i";
    script = ''
      set -uo pipefail
      run=$1
      args=()
      # dry-<run>-<attempt> from GitHub's runner, forge-dry-<run>-<attempt> from Forge's (feat/forge-deploy.nix).
      case "$run" in dry-* | forge-dry-*) args+=(--build-only) ;; esac
      if [ -e ${state}/hold ]; then
        echo "::error::Cluster releases are held: $(head -c 2000 ${state}/hold | tr '\n' ' ')(${state}/hold on ${host}: remove it, then run the workflow again)"
        exit 1
      fi
      exec 9>${state}/lock
      if ! flock -w 3600 9; then
        echo "::error::Another release still holds ${state}/lock after an hour"
        exit 1
      fi
      checkout=${state}/checkout
      if [ ! -d "$checkout/.git" ]; then
        rm -rf "$checkout"
        git clone --quiet --no-checkout git@github.com:xmit-dev/ultimator.git "$checkout" || exit 1
      fi
      cd "$checkout" || exit 1
      git fetch --quiet origin main || exit 1
      main=$(git rev-parse FETCH_HEAD)
      git checkout --quiet --force --detach "$main" && git clean --quiet -ffdx || exit 1
      if [ ! -x deploy/nixos/release.sh ]; then
        echo "::error::GitHub's main (''${main:0:12}) has no deploy/nixos/release.sh"
        exit 1
      fi
      export CLUSTER_KNOWN_HOSTS=/etc/ultimator-cluster/known-hosts
      export CLUSTER_SSH_KEY=${state}/deploy-key
      if [ ! -r "$CLUSTER_SSH_KEY" ]; then
        echo "::warning::$CLUSTER_SSH_KEY isn't there yet: releasing with ${user}'s own ~/.ssh/id_ed25519"
        CLUSTER_SSH_KEY=$HOME/.ssh/id_ed25519
      fi
      echo "releasing ''${main:0:12} (run $run)"
      exec deploy/nixos/release.sh "''${args[@]}"
    '';
  };
}
