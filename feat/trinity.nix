# Trinity's development stack, moved here from the Mac: ~/src/trinity next to
# ~/src/flower, under process-compose in Trinity's dev shell (direnv), as
# pcarrier. State stays in ~/src/trinity/.dev, secrets in its .env.local; the
# unit starts once .env.local is there. `process-compose --use-uds
# --unix-socket ~/src/trinity/.dev/pc.sock attach` (or `process logs NAME`).
#
# Browsers reach it at https://trinity.pcarrier.com through nginx, behind HTTP
# basic auth (/etc/trinity.htpasswd, outside the store, like /etc/code.htpasswd):
# the stack keeps TRINITY_DEV_LOGIN, with which anyone who reaches the gateway
# signs in as anyone. The gateway itself listens on loopback and the tailnet,
# over plain HTTP, for the stack's own processes and the CLI. In .env.local:
#   TRINITY_URL=http://127.0.0.1:8301
#   TRINITY_PUBLIC_URL=https://trinity.pcarrier.com
#   TRINITY_HOST=127.0.0.1,<`tailscale ip -4`>
{ lib, pkgs, ... }:
let
  root = "/home/pcarrier/src/trinity";
  domain = "trinity.pcarrier.com";
in
{
  # The stack's `sandboxes` process runs agents' sandbox computers in Docker.
  virtualisation.docker.enable = true;
  users.users.pcarrier.extraGroups = [ "docker" ];

  # Headless Chromium to check the web client over CDP.
  environment.systemPackages = [ pkgs.chromium ];

  # Sessions and memory written on the Mac name /Users/pcarrier/… paths.
  systemd.tmpfiles.rules = [
    "d /Users 0755 root root -"
    "L+ /Users/pcarrier - - - - /home/pcarrier"
  ];

  systemd.services.trinity = {
    description = "Trinity development stack";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [
      "network-online.target"
      "docker.service"
      "tailscaled.service"
    ];
    unitConfig.ConditionPathExists = "${root}/.env.local";
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

  services.nginx = {
    enable = true;
    recommendedProxySettings = true;
    virtualHosts.${domain} = {
      enableACME = true;
      forceSSL = true;
      locations."/" = {
        proxyPass = "http://127.0.0.1:8301";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_buffering off;
          proxy_request_buffering off;
          client_max_body_size 1g;
          # Watches (WebSocket and SSE) stay open while nothing changes.
          proxy_read_timeout 1d;
          proxy_send_timeout 1d;
        '';
      };
    };
  };
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
}
