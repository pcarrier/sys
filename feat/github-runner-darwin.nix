# A GitHub Actions runner for the xmit-dev organisation on a Mac, named after
# the host and labelled `nix` and the host's name, next to GitHub's own
# `self-hosted`, `macOS` and `ARM64`: `runs-on: [self-hosted, crab]`.
# feat/github-runner.nix is the NixOS one; the token file works the same way.
#
# nix-darwin has services.github-runners, but it asserts nix.enable, which is
# off on these Macs (Determinate runs Nix). So this is its launchd daemon
# spelled out, with two changes: it registers again when the token file or the
# registration changes, as the NixOS module does, and it registers as root,
# handing the token to the runner in its environment, so the jobs (which run
# as _github-runner) can't read the token file.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  host = config.networking.hostName;
  user = "_github-runner";
  # uid and gid 533, as nix-darwin's module picks: free on these Macs, and
  # hidden from the login window.
  id = 533;
  url = "https://github.com/xmit-dev";
  labels = [
    "nix"
    host
  ];
  tokenFile = "/var/lib/secrets/github-runner.token";

  base = "/var/lib/github-runner";
  root = "${base}/${host}"; # RUNNER_ROOT: .runner, .credentials; the runner's
  work = "${base}/_work/${host}"; # jobs' workspaces, emptied at each start
  registered = "${base}/.${host}.registered"; # root's alone: what was registered
  logs = "/var/log/github-runner";

  runner = pkgs.github-runner;
  registration = pkgs.writeText "github-runner-${host}.json" (
    builtins.toJSON {
      inherit url labels work;
      name = host;
    }
  );
  # What jobs find on PATH: the runner's own needs, what actions reach for and
  # what xmit-dev/ultimator's macOS workflows call (rustup, npx, xcodegen), then
  # Determinate's Nix and the system's, whose /usr/bin shims reach the Xcode
  # command line tools (clang, lipo, notarytool, stapler). Those
  # aren't Nix's: `softwareupdate` installs them, as Chrome for browser.yml
  # goes in /Applications and a full Xcode for mobile-ios.yml comes from the
  # App Store.
  path = lib.makeBinPath (
    with pkgs;
    [
      bashInteractive
      coreutils
      curl
      file
      findutils
      gawk
      git
      gnugrep
      gnused
      gnutar
      gzip
      jq
      nodejs
      openssh
      rustup
      unzip
      which
      xcodegen
      xz
      zip
      zstd
    ]
  );
  env = [
    "HOME=${work}"
    "RUNNER_ROOT=${root}"
    "PATH=${path}:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    "LANG=en_US.UTF-8"
    "NIX_SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
  ];
  # sudo resets the environment; env sets what the runner gets. Registration
  # alone passes the token through, in the environment rather than argv.
  asRunner = "/usr/bin/sudo -u ${user} -- /usr/bin/env -i ${lib.escapeShellArgs env}";
  registerAsRunner = "/usr/bin/sudo --preserve-env=ACTIONS_RUNNER_INPUT_PAT,ACTIONS_RUNNER_INPUT_TOKEN -u ${user} -- /usr/bin/env ${lib.escapeShellArgs env}";
in
{
  users = {
    knownUsers = [ user ];
    knownGroups = [ user ];
    users.${user} = {
      uid = id;
      gid = id;
      home = base;
      createHome = false;
      shell = "/bin/bash";
      isHidden = true;
      description = "GitHub Actions runner";
    };
    groups.${user} = {
      gid = id;
      description = "GitHub Actions runner";
    };
  };

  # After the user exists and before its daemon loads.
  system.activationScripts.launchd.text = lib.mkBefore ''
    echo >&2 "setting up the GitHub Actions runner..."
    install -d -m 0700 -o root -g wheel /var/lib/secrets
    install -d -m 0755 -o root -g wheel ${base} ${base}/_work
    install -d -m 0700 -o ${toString id} -g ${toString id} ${root} ${work}
    install -d -m 0700 -o root -g wheel ${registered}
    install -d -m 0755 -o root -g wheel ${logs}
  '';

  launchd.daemons.github-runner = {
    script = ''
      set -euo pipefail

      if [[ ! -s ${tokenFile} ]]; then
        echo "no token in ${tokenFile}" >&2
        exit 1
      fi

      # A new token or a new registration: forget the old one.
      if ! cmp -s ${registration} ${registered}/registration.json ||
        ! cmp -s ${tokenFile} ${registered}/token; then
        echo "registration changed, registering again" >&2
        find ${root} ${registered} -mindepth 1 -delete
      fi

      find ${work} -mindepth 1 -delete

      if [[ ! -f ${root}/.runner ]]; then
        token=$(<${tokenFile})
        # The runner reads ACTIONS_RUNNER_INPUT_<ARG> as --<arg>.
        if [[ $token =~ ^gh[a-z]+_ || $token =~ ^github_pat_ ]]; then
          export ACTIONS_RUNNER_INPUT_PAT=$token
        else
          export ACTIONS_RUNNER_INPUT_TOKEN=$token
        fi
        unset token
        ${registerAsRunner} ${runner}/bin/config.sh \
          --unattended \
          --disableupdate \
          --replace \
          --url ${url} \
          --name ${host} \
          --labels ${lib.concatStringsSep "," labels} \
          --work ${work}
        unset ACTIONS_RUNNER_INPUT_PAT ACTIONS_RUNNER_INPUT_TOKEN
        cp ${registration} ${registered}/registration.json
        cp ${tokenFile} ${registered}/token
      fi

      exec ${asRunner} ${runner}/bin/Runner.Listener run --startuptype service
    '';
    serviceConfig = {
      RunAtLoad = true;
      KeepAlive = true;
      ThrottleInterval = 30;
      ProcessType = "Interactive";
      # A security session of its own, as a login has: without one, codesign can't use
      # the private key of a keychain a job makes and unlocks (errSecInternalComponent),
      # which signing the desktop app does.
      SessionCreate = true;
      WorkingDirectory = root;
      StandardOutPath = "${logs}/${host}.log";
      StandardErrorPath = "${logs}/${host}.log";
    };
  };
}
