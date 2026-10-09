# What indentbox keeps of Ultimator, whose production runs on the i0–i2 cluster since 10-06.
# Retired: the development stack (the `ultimator` unit, process-compose, its Flower voter 1, 10-09;
# its data in /src/ultimator/.dev/flower is deleted), Garage (10-08: the blobs live on fsn1),
# Caddy's ultimator.app, www and *.yas.ultimator.app sites with their certificates and the
# gateway's UDP ports 444 and 4433 (10-08). Left: Caddy (below), which serves
# trinity.pcarrier.com's redirect to ultimator.app and hosts/indentbox.nix's YAS sites, and
# Docker, Chromium and /Users, kept as they were.
{ lib, pkgs, ... }:
let
  domain = "ultimator.app";
  redirects = [
    "trinity.pcarrier.com"
  ];
  # Certificates stay lego's (security.acme): HTTP-01 through the webroot that
  # Caddy's port 80 serves (below).
  webroot = "/var/lib/acme/acme-challenge";
in
{
  # Docker, kept as it was (the retired stack's `sandboxes` process ran agents' computers in it).
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
