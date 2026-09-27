# Trinity's development stack, as it runs on the Mac: ~/src/trinity next to
# ~/src/flower, under process-compose in Trinity's dev shell (direnv), as
# pcarrier. State stays in ~/src/trinity/.dev; secrets in its .env.local.
#
# Before the first start: clone both repositories into ~/src, `direnv allow`
# in ~/src/trinity, and bring .env.local and .dev over (or start fresh).
# Then `sudo systemctl start trinity`; `process-compose --use-uds --unix-socket
# ~/src/trinity/.dev/pc.sock attach` (or `process logs NAME`) from a shell.
#
# The web client and API stay on the tailnet, at https://indentbox.tail10cd.ts.net:8301,
# with a Let's Encrypt certificate from Tailscale that every browser trusts:
# with TRINITY_DEV_LOGIN anyone who reaches the gateway signs in as anyone.
# In .env.local:
#   TRINITY_URL=https://indentbox.tail10cd.ts.net:8301
#   TRINITY_HOST=127.0.0.1,<`tailscale ip -4`>
#   TRINITY_TLS_CERT_FILE=/var/lib/trinity-tls/cert.pem
#   TRINITY_TLS_KEY_FILE=/var/lib/trinity-tls/key.pem
{
  config,
  lib,
  pkgs,
  ...
}:
let
  root = "/home/pcarrier/src/trinity";
  domain = "${config.networking.hostName}.tail10cd.ts.net";
in
{
  # The stack's `sandboxes` process runs agents' sandbox computers in Docker.
  virtualisation.docker.enable = true;
  users.users.pcarrier.extraGroups = [ "docker" ];

  # Headless Chromium to check the web client over CDP.
  environment.systemPackages = [ pkgs.chromium ];

  systemd.services.trinity = {
    description = "Trinity development stack";
    # Not started at boot yet: the Mac still runs the stack, and two stacks
    # would share one Slack app's Socket Mode connection. At the cutover:
    # wantedBy = [ "multi-user.target" ];
    wants = [
      "network-online.target"
      "trinity-tls.service"
    ];
    after = [
      "network-online.target"
      "docker.service"
      "tailscaled.service"
      "trinity-tls.service"
    ];
    unitConfig.ConditionPathExists = "${root}/.envrc";
    environment.PC_DISABLE_TUI = "1";
    serviceConfig = {
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = root;
      # A login shell, so the agent's local computer has the same tools as an
      # interactive shell (git signs and pushes with ~/.ssh/id_ed25519);
      # bin/dev then enters the dev shell with direnv.
      ExecStart = "${lib.getExe pkgs.fish} -l -c 'exec ${root}/bin/dev'";
      Restart = "on-failure";
      RestartSec = 10;
      # process-compose stops its processes in order; Flower flushes on SIGTERM.
      KillMode = "mixed";
      TimeoutStopSec = 90;
      LimitNOFILE = 1048576;
    };
  };

  # The gateway's certificate, renewed daily by tailscaled (which caches it
  # and asks Let's Encrypt again only near expiry). A new one takes effect
  # when the gateway restarts, so restart it when the certificate changed.
  systemd.services.trinity-tls = {
    description = "Tailscale certificate for Trinity's gateway";
    wants = [ "network-online.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
    ];
    path = [
      config.services.tailscale.package
      pkgs.coreutils
      pkgs.process-compose
      pkgs.util-linux
    ];
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "trinity-tls";
      StateDirectoryMode = "0755";
    };
    script = ''
      cd /var/lib/trinity-tls
      before="$(cat cert.pem 2>/dev/null | sha256sum)"
      tailscale cert --cert-file cert.pem --key-file key.pem ${domain}
      chown pcarrier:users cert.pem key.pem
      chmod 0644 cert.pem
      chmod 0600 key.pem
      if [ "$before" != "$(sha256sum < cert.pem)" ] && [ -S ${root}/.dev/pc.sock ]; then
        runuser -u pcarrier -- process-compose --use-uds --unix-socket ${root}/.dev/pc.sock \
          process restart gateway || true
      fi
    '';
  };
  systemd.timers.trinity-tls = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      OnBootSec = "1min";
      Persistent = true;
      RandomizedDelaySec = "1h";
    };
  };
}
