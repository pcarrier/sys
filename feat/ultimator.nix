# Ultimator's old development stack, retired 10-07: production runs on the i0–i2
# cluster since 10-06. What is left here: /src/ultimator next to /src/flower
# (under /src so agents' sandboxes, which bind it, read the code), under
# process-compose in its dev shell (direnv), as pcarrier. State stays in
# /src/ultimator/.dev, secrets in its .env.local; the unit starts once
# .env.local is there. `.dev/deploy-keep-stopped` keeps its app down.
# `process-compose --use-uds --unix-socket /src/ultimator/.dev/pc.sock attach`
# (or `process logs NAME`).
#
# Gone 10-08 (Pierre: "Garage cleanup, and drop Caddy"): Garage (the stack's blobs
# live on fsn1 since the 10-07 blob switch), Caddy's ultimator.app, www and
# *.yas.ultimator.app sites with their certificates, and the gateway's UDP ports
# (444, 4433). Caddy stays for trinity.pcarrier.com's redirect and
# hosts/indentbox.nix's YAS sites.
{ lib, pkgs, ... }:
let
  root = "/src/ultimator";
  domain = "ultimator.app";
  redirects = [
    "trinity.pcarrier.com"
  ];
  # Certificates stay lego's (security.acme): HTTP-01 through the webroot that
  # Caddy's port 80 serves (below).
  webroot = "/var/lib/acme/acme-challenge";
in
{
  # The stack's `sandboxes` process runs agents' sandbox computers in Docker.
  virtualisation.docker.enable = true;
  users.users.pcarrier.extraGroups = [ "docker" ];

  # UDP 443 is Caddy's HTTP/3.
  networking.firewall.allowedUDPPorts = [
    443
  ];

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
    # garage.service is gone (10-08); the names stay so that the unit file, and
    # with it this retired stack, is not restarted by the switch that removed it.
    wants = [
      "network-online.target"
      "garage.service"
    ];
    after = [
      "network-online.target"
      "docker.service"
      "tailscaled.service"
      "garage.service"
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

  # Caddy terminates HTTPS for every name here (hosts/indentbox.nix adds YAS's
  # own), over HTTP/1.1, HTTP/2 and HTTP/3. Its admin API, which reloads use,
  # listens on a socket only caddy opens rather than on localhost:2019, where
  # anyone here could change what it serves (and serve its keys).
  #
  # The access log (/var/log/caddy/access.log, JSON, kept 26 weeks) keeps no
  # query strings and no headers but the user agent and the referrer's path.
  # Each site logs there with `import logged` and `logFormat = null`, here and in
  # hosts/indentbox.nix: the module's default logFormat writes a file of the
  # site's own, headers whole. (Error lines, in the journal, may still quote a
  # request.)
  services.caddy = {
    enable = true;
    globalConfig = ''
      admin "unix//var/lib/caddy/admin.sock|0600"
      auto_https off
      log access {
        output file /var/log/caddy/access.log {
          roll_size 100MiB
          roll_keep 1000
          roll_keep_for 4368h
        }
        format filter {
          wrap json
          fields {
            request>uri regexp `\?.*` ""
            request>headers delete
            resp_headers delete
            referer regexp `\?.*` ""
          }
        }
        include http.log.access
      }
      servers {
        0rtt off
      }
    '';
    extraConfig = ''
      (logged) {
        log
        log_append user_agent {http.request.header.User-Agent}
        log_append referer {http.request.header.Referer}
      }
    '';
    virtualHosts = {
      # Port 80, for every name: lego's HTTP-01 answers, and HTTPS for the rest.
      "http://" = {
        logFormat = null;
        extraConfig = ''
          import logged
          handle /.well-known/acme-challenge/* {
            root * ${webroot}
            file_server
          }
          handle {
            redir https://{host}{uri} 301
          }
        '';
      };
    }
    # Old links (Slack messages, bookmarks) land on the same page there; 308
    # keeps the method and body, for clients that still post to the old name.
    // lib.genAttrs redirects (name: {
      hostName = "https://${name}";
      useACMEHost = name;
      logFormat = null;
      extraConfig = ''
        import logged
        redir https://${domain}{uri} 308
      '';
    });
  };
  security.acme.certs = lib.genAttrs redirects (_: {
    inherit webroot;
  });
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
}
