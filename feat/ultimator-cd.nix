# Continuous deployment of Ultimator on the host that runs its stack (feat/ultimator.nix): each push to
# xmit-dev/ultimator's main goes live, through .github/workflows/deploy-main.yml there.
#
# A GitHub Actions runner of xmit-dev's, `<host>`, labelled `indentbox` and `deploy` and with none of
# GitHub's default labels (`self-hosted`, `Linux`, `X64`), so only jobs naming those land here, never the
# org's other self-hosted jobs; in the runner group `deploy`, which only xmit-dev/ultimator may use. Its user, github-runner, may do one thing beyond what any user may (polkit,
# below): start ultimator-deploy-main@<run>.service, where <run> is the workflow run's ID and attempt
# (`dry-` first for a dry run). /src, where the stack keeps its data, is out of its sight.
#
# Each such unit runs, as the stack's user in its checkout: first the hold (while .dev/deploy-hold is
# there, deploys and dry runs alike stop, saying why, before anything is fetched), then `git fetch origin
# main` and main's own bin/deploy-main, which moves the checkout to main and waits for the stack's
# watchers (what it does is said there). Its output goes to /var/log/ultimator-deploy-main/<run>.log,
# which the job follows; the logs go after 30 days. A deploy goes on to its end when the job stops early
# (a cancelled run, this runner restarting).
#
# The runner registers with /var/lib/secrets/github-runner.token, as in feat/github-runner.nix: a
# registration token (org › Settings › Actions › Runners › New runner, or `gh api -X POST
# orgs/xmit-dev/actions/runners/registration-token --jq .token`; good for an hour), written without a
# trailing newline (`printf %s`). It keeps its own credentials in /var/lib/github-runner/<host> once
# registered.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  host = config.networking.hostName;
  root = "/src/ultimator";
  user = "pcarrier";
  logs = "/var/log/ultimator-deploy-main";
  work = "/var/lib/github-runner-work";
in
{
  users = {
    users.github-runner = {
      isSystemUser = true;
      group = "github-runner";
      home = work;
      description = "GitHub Actions runner";
    };
    groups.github-runner = { };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/secrets 0700 root root -"
    "d ${work} 0750 github-runner github-runner -"
    "d ${work}/${host} 0750 github-runner github-runner -"
    "d ${logs} 0755 ${user} users 30d"
  ];

  services.github-runners.${host} = {
    enable = true;
    url = "https://github.com/xmit-dev";
    tokenFile = "/var/lib/secrets/github-runner.token";
    name = host;
    replace = true;
    # xmit-dev's runner group `deploy`, which only xmit-dev/ultimator's workflows may use.
    runnerGroup = "deploy";
    noDefaultLabels = true;
    extraLabels = [
      "indentbox"
      "deploy"
    ];
    user = "github-runner";
    group = "github-runner";
    workDir = "${work}/${host}";
    serviceOverrides = {
      # The stack's checkout and data (Flower's, the logs, the workspace), and its host-local overrides.
      InaccessiblePaths = [
        "/src"
        "-/var/lib/ultimator"
      ];
      # The production host: jobs here start a unit and follow its log, nothing heavier.
      CPUWeight = 20;
      IOWeight = 20;
      MemoryHigh = "2G";
      MemoryMax = "4G";
    };
  };

  security.polkit.extraConfig = ''
    // The GitHub runner (feat/ultimator-cd.nix) may start deploys of Ultimator's main, and nothing else.
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "github-runner" &&
          action.lookup("verb") == "start" &&
          /^ultimator-deploy-main@(dry-)?[0-9]+-[0-9]+\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
    });
  '';

  systemd.services."ultimator-deploy-main@" = {
    description = "Deploy Ultimator's main to the stack (run %i)";
    after = [ "ultimator.service" ];
    # The setuid wrappers first (sudo: /run/current-system/sw has a sudo without the setuid bit, which refuses
    # to run), then as the stack gets them: its user's tools (git, direnv), then the system's (nix, ssh, curl).
    path = [
      "/run/wrappers"
      "/etc/profiles/per-user/${user}"
      "/run/current-system/sw"
    ];
    environment.DEPLOY_MAIN_ANNOTATE = "1";
    # A deploy under way goes on to its end through a switch.
    restartIfChanged = false;
    stopIfChanged = false;
    serviceConfig = {
      Type = "oneshot";
      User = user;
      Group = "users";
      WorkingDirectory = root;
      RuntimeDirectory = "ultimator-deploy-main/%i";
      StandardOutput = "truncate:${logs}/%i.log";
      StandardError = "inherit";
      TimeoutStartSec = "110min";
    };
    # Each run its own instance: gone once it ends, failed or not (its log stays). A [Unit] setting: under
    # [Service], systemd ignores it, and failed runs stayed loaded.
    unitConfig.CollectMode = "inactive-or-failed";
    scriptArgs = "%i";
    script = ''
      run=$1
      args=()
      case "$run" in dry-*) args+=(--dry-run) ;; esac
      if [ -e .dev/deploy-hold ]; then
        echo "::error::Deploys are held: $(head -c 2000 .dev/deploy-hold | tr '\n' ' ')(${root}/.dev/deploy-hold on ${host}: remove it, then run the workflow again)"
        exit 1
      fi
      git fetch --quiet origin main
      main=$(git rev-parse refs/remotes/origin/main)
      if ! git show "$main:bin/deploy-main" >"$RUNTIME_DIRECTORY/deploy-main" 2>/dev/null; then
        echo "::error::GitHub's main (''${main:0:12}) has no bin/deploy-main"
        exit 1
      fi
      exec bash "$RUNTIME_DIRECTORY/deploy-main" "''${args[@]}" "$main"
    '';
  };
}
