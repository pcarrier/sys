# Continuous deployment of Ultimator to its cluster (i0, i1 and i2, ulti/ultimator's deploy/nixos): each push to
# ulti/ultimator's main on Forge goes live through .ultimator/workflows/deploy.ts there, whose job runs on this host
# (feat/forge-deploy.nix). What follows describes the unit both start; the GitHub Actions runner below is what
# .github/workflows/deploy-main.yml used, which is gone (#975): the runner can go once Forge's deploys are trusted.
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
#   - main, fetched from Forge (https://ultimator.app/git/ulti/ultimator.git) into a clean checkout of its own
#     (/var/lib/ultimator-cluster-release/checkout: reset and cleaned each run, never anyone's working tree). Forge
#     asks for a credential, and nothing here may wait on a person's sign-in (a CLI sign-in ends within 7 days): the
#     unit's git (and Nix's, for the Flower input, and deploy/cd.sh's fetches of the flake.lock pins) reads a READ TOKEN
#     from /var/lib/ultimator-cluster-release/forge-read-token, the token's password alone (0600, pcarrier's; the
#     token is read-only, for ulti/ultimator and ulti/flower, expiring and revocable) through a helper that only ever
#     answers (git's own `store` would erase the file at the first refusal). Make it, as pcarrier on a machine signed in
#     to Ultimator as an integration manager:
#       ultimator repo token ulti/ultimator ulti/flower --days 90 --label hound-release \
#         | sed -n 's/^password: //p' > /var/lib/ultimator-cluster-release/forge-read-token.new \
#         && chmod 600 /var/lib/ultimator-cluster-release/forge-read-token.new \
#         && mv /var/lib/ultimator-cluster-release/forge-read-token.new /var/lib/ultimator-cluster-release/forge-read-token
#     (a pipe, so the secret reaches no terminal or log) and make it again before it expires (`ultimator repo tokens`
#     lists it; the unit warns in its log from 14 days before it ends);
#   - its deploy/cd.sh (deploy/nixos/release.sh on a main from before it; --build-only for a dry run), which runs
#     release.sh as before unless ${state}/mode says `releases` (feat/ultimator-release-build.nix), with CLUSTER_KNOWN_HOSTS the hosts' installed keys
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
  pkgs,
  ...
}:
let
  forge = "https://ultimator.app/git/ulti/ultimator.git";
  # Git's credential helper for Forge: the read token, as `username=read` and its password, for a request to get one.
  forgeCredential = pkgs.writeShellScript "forge-read-token-helper" ''
    [ "$1" = get ] || exit 0
    printf 'username=read\npassword=%s\n' "$(cat ${state}/forge-read-token)"
  '';
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
    # Git's (and Nix's, which runs it for the Flower input) own configuration for Forge: the read token through a
    # helper, never a prompt. Only this unit: pcarrier's own git config is his.
    environment = {
      GIT_TERMINAL_PROMPT = "0";
      GIT_CONFIG_COUNT = "1";
      GIT_CONFIG_KEY_0 = "credential.https://ultimator.app.helper";
      GIT_CONFIG_VALUE_0 = "${forgeCredential}";
    };
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
      if [ ! -r ${state}/forge-read-token ]; then
        echo "::error::${state}/forge-read-token isn't there: Forge's git needs a read token (feat/ultimator-cluster-cd.nix says how to make one)"
        exit 1
      fi
      # The token is an Ultimator one with an end date, which only its maker's `ultimator repo tokens` shows: say when
      # the file is old, the way to know a month ahead that the next release would fail.
      if [ -n "$(find ${state}/forge-read-token -mtime +76 -print)" ]; then
        echo "::warning::${state}/forge-read-token is over 76 days old: its token (90 days) ends soon, make another (ultimator repo token)"
      fi
      checkout=${state}/checkout
      if [ ! -d "$checkout/.git" ]; then
        rm -rf "$checkout"
        git clone --quiet --no-checkout ${forge} "$checkout" || exit 1
      fi
      cd "$checkout" || exit 1
      # A checkout from before Forge still has GitHub's URL as its origin.
      git remote set-url origin ${forge}
      git fetch --quiet origin main || exit 1
      main=$(git rev-parse FETCH_HEAD)
      git checkout --quiet --force --detach "$main" && git clean --quiet -ffdx || exit 1
      # deploy/cd.sh decides how main goes out (its ${state}/mode: absent, today's deploy/nixos/release.sh; `releases`,
      # releases from artifacts, xmit-dev/ultimator's "deploy in a minute"); a main from before it has release.sh alone.
      entry=deploy/cd.sh
      [ -x "$entry" ] || entry=deploy/nixos/release.sh
      if [ ! -x "$entry" ]; then
        echo "::error::Forge's main (''${main:0:12}) has neither deploy/cd.sh nor deploy/nixos/release.sh"
        exit 1
      fi
      export CLUSTER_KNOWN_HOSTS=/etc/ultimator-cluster/known-hosts
      export CLUSTER_SSH_KEY=${state}/deploy-key
      if [ ! -r "$CLUSTER_SSH_KEY" ]; then
        echo "::warning::$CLUSTER_SSH_KEY isn't there yet: releasing with ${user}'s own ~/.ssh/id_ed25519"
        CLUSTER_SSH_KEY=$HOME/.ssh/id_ed25519
      fi
      echo "releasing ''${main:0:12} (run $run, $entry)"
      exec "$entry" "''${args[@]}"
    '';
  };
}
