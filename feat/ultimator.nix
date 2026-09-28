# Ultimator's development stack: /src/ultimator next to /src/flower (under /src
# so agents' sandboxes, which bind it, read the code), under process-compose in
# its dev shell (direnv), as pcarrier. State stays in /src/ultimator/.dev,
# secrets in its .env.local; the unit starts once .env.local is there.
# `process-compose --use-uds --unix-socket /src/ultimator/.dev/pc.sock attach`
# (or `process logs NAME`).
#
# Browsers reach it at https://ultimator.app through nginx; its passkeys guard
# it, and passkeys belong to that name. trinity.pcarrier.com, its old name, and
# www.ultimator.app redirect there, keeping the path. The gateway itself listens
# on loopback and the tailnet, over plain HTTP, for the stack's own processes
# and the CLI. In .env.local:
#   ULTIMATOR_URL=http://127.0.0.1:8301
#   ULTIMATOR_PUBLIC_URL=https://ultimator.app
#   ULTIMATOR_HOST=127.0.0.1,indentbox.tail10cd.ts.net
#
# Computers' YAS workspaces are framed from an origin of their own,
# https://<computer>.yas.ultimator.app, which the same gateway serves by Host
# (YAS runs a computer's own web pages on that origin, so never on
# ultimator.app's). One wildcard certificate covers them: DNS-01 through
# Namecheap's API, whose credentials (NAMECHEAP_API_USER, NAMECHEAP_API_KEY;
# indentbox's address whitelisted there) live in /var/lib/secrets/acme-namecheap.env, root's
# only. With the `*.yas` A record in place, in .env.local:
#   ULTIMATOR_YAS_URL=https://*.yas.ultimator.app
{ lib, pkgs, ... }:
let
  root = "/src/ultimator";
  domain = "ultimator.app";
  redirects = [
    "trinity.pcarrier.com"
    "www.ultimator.app"
  ];
  frames = "yas.${domain}";
  namecheapEnv = "/var/lib/secrets/acme-namecheap.env";
  gateway = {
    proxyPass = "http://127.0.0.1:8301";
    extraConfig = ''
      proxy_buffering off;
    '';
  };
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

  systemd.services.ultimator = {
    description = "Ultimator development stack";
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
    virtualHosts = {
      ${domain} = {
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
    }
    # Old links (Slack messages, bookmarks) land on the same page there; 308
    # keeps the method and body, for clients that still post to the old name.
    // lib.genAttrs redirects (_: {
      enableACME = true;
      forceSSL = true;
      globalRedirect = domain;
      redirectCode = 308;
    })
    # The frames: the gateway answers only the frame's own files there.
    // {
      "*.${frames}" = {
        useACMEHost = frames;
        forceSSL = true;
        locations."/" = gateway;
      };
    };
  };
  security.acme.certs.${frames} = {
    domain = "*.${frames}";
    dnsProvider = "namecheap";
    environmentFile = namecheapEnv;
    group = "nginx";
  };
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
}
