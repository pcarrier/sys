# Ultimator's development stack: /src/ultimator next to /src/flower (under /src
# so agents' sandboxes, which bind it, read the code), under process-compose in
# its dev shell (direnv), as pcarrier. State stays in /src/ultimator/.dev,
# secrets in its .env.local; the unit starts once .env.local is there.
# `process-compose --use-uds --unix-socket /src/ultimator/.dev/pc.sock attach`
# (or `process logs NAME`).
#
# Browsers reach it at https://ultimator.app through Caddy; its passkeys guard
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
#
# The stack's blobs (sealed history, attachments, long outputs, pictures) live
# in Garage, an S3-compatible object store, on loopback alone: one node, one
# copy, synced writes, as the directory store it replaced. Its RPC secret and
# admin token (GARAGE_RPC_SECRET, GARAGE_ADMIN_TOKEN) live in
# /var/lib/secrets/garage.env, root's only; `sudo garage …` administers it
# (status, bucket info ultimator, key info ultimator). The stack's key, made
# with `garage key create ultimator` and allowed on bucket `ultimator`, goes
# in .env.local:
#   ULTIMATOR_BLOBS=s3://ultimator
#   ULTIMATOR_S3_ENDPOINT=http://127.0.0.1:3900
#   ULTIMATOR_S3_REGION=garage
#   ULTIMATOR_S3_ACCESS_KEY_ID=GK…
#   ULTIMATOR_S3_SECRET_ACCESS_KEY=…
# While `ultimatord blobs copy /src/ultimator/.dev/blobs` moves the old
# directory store's blobs in, ULTIMATOR_BLOBS_FALLBACK=file:///src/ultimator/.dev/blobs
# lets reads find those not moved yet (writes go to Garage alone).
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
  garageEnv = "/var/lib/secrets/garage.env";
  # Responses go out as the gateway writes them (watches, the log), request
  # bodies stream in, and streams open when Caddy reloads (certificate renewals
  # reload it) get ten minutes to end on their own rather than being cut.
  gateway = ''
    reverse_proxy 127.0.0.1:8301 {
      flush_interval -1
      stream_close_delay 10m
    }
  '';
  # Certificates stay lego's (security.acme), as under nginx: HTTP-01 through
  # the webroot that Caddy's port 80 serves (below), DNS-01 for the frames'
  # wildcard.
  webroot = "/var/lib/acme/acme-challenge";
in
{
  # The stack's `sandboxes` process runs agents' sandbox computers in Docker.
  virtualisation.docker.enable = true;
  users.users.pcarrier.extraGroups = [ "docker" ];

  # The gateway's YAS uplink relay (ULTIMATOR_UPLINK_PORT=4433 in .env.local):
  # WebTransport over UDP, where computers' `yas uplink` producers hold their
  # sessions, at https://ultimator.app:4433 with the self-signed certificate
  # their relay addresses pin. Not 443: Caddy's HTTP/3 takes UDP 443.
  # UDP 444 is where nginx served HTTP/3 (Alt-Svc h3=":444", kept by browsers
  # for a day): Caddy answers there too until those entries have expired.
  networking.firewall.allowedUDPPorts = [
    443
    444
    4433
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
    # Garage holds the blobs; a stack started without it fails every blob read.
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

  services.garage = {
    enable = true;
    package = pkgs.garage_2;
    environmentFile = garageEnv;
    settings = {
      replication_factor = 1;
      db_engine = "lmdb";
      # Written through before S3 answers, as the directory store synced its files: the log's
      # sealed events leave Flower once their copy is here.
      metadata_fsync = true;
      data_fsync = true;
      metadata_auto_snapshot_interval = "6h";
      rpc_bind_addr = "127.0.0.1:3901";
      rpc_public_addr = "127.0.0.1:3901";
      s3_api = {
        s3_region = "garage";
        api_bind_addr = "127.0.0.1:3900";
      };
      admin.api_bind_addr = "127.0.0.1:3903";
    };
  };

  # Caddy terminates HTTPS for every name here (hosts/indentbox.nix adds YAS's
  # own), over HTTP/1.1, HTTP/2 and HTTP/3 (one round trip to connect rather
  # than TCP's and TLS's two, and no stall of every stream on one lost packet).
  # It replaced nginx on 10-01: Apple's QUIC client gives itself no connection
  # ID (a zero-length one), and once such a client's address changed (a NAT
  # rebinding, Wi-Fi to cellular, the app coming back on screen), nginx's QUIC
  # dropped every packet it sent, wanting a connection ID of the client's for
  # the new path that the client can't give: the phone app's requests hung
  # until iOS gave up on the connection, 5 to 26 s later. quic-go, under Caddy,
  # moves the connection to the new path. No 0-RTT: it would let a replayed
  # POST run twice. Its admin API, which reloads use, listens on a socket only
  # caddy opens rather than on localhost:2019, where anyone here could change
  # what it serves (and serve its keys).
  #
  # The access log (/var/log/caddy/access.log, JSON, kept 26 weeks) keeps no
  # query strings and no headers but the user agent and the referrer's path:
  # Ultimator's web client used to put people's sign-in tokens in queries
  # (/blobs/…?token=, /drafts/…?token=), and queries still carry OAuth codes and
  # states and short-lived grants. Each site logs there with `import logged`
  # and `logFormat = null`, here and in hosts/indentbox.nix: the module's
  # default logFormat writes a file of the site's own, headers whole. (Error
  # lines, in the journal, may still quote a request.)
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
      servers :444 {
        protocols h3
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
      ${domain} = {
        hostName = "https://${domain}";
        serverAliases = [ "https://${domain}:444" ];
        useACMEHost = domain;
        logFormat = null;
        extraConfig = ''
          import logged
          ${gateway}
        '';
      };
      # The frames: the gateway answers only the frame's own files there.
      "*.${frames}" = {
        hostName = "https://*.${frames}";
        useACMEHost = frames;
        logFormat = null;
        extraConfig = ''
          import logged
          ${gateway}
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
  security.acme.certs =
    lib.genAttrs ([ domain ] ++ redirects) (_: {
      inherit webroot;
    })
    // {
      ${frames} = {
        domain = "*.${frames}";
        dnsProvider = "namecheap";
        environmentFile = namecheapEnv;
        # Namecheap's own servers, not a caching resolver (the tailnet's), which may
        # still hold the wildcard's CNAME for _acme-challenge.yas after lego writes
        # the TXT record there.
        dnsResolver = "dns1.registrar-servers.com:53";
      };
    };
  # The *.yas CNAME covers _acme-challenge.yas too, until the TXT record is
  # there. lego would follow it and wait for the record at ultimator.app, while
  # its Namecheap provider writes it at _acme-challenge.yas, which Let's
  # Encrypt reads: don't follow CNAMEs.
  systemd.services."acme-order-renew-${frames}".environment.LEGO_DISABLE_CNAME_SUPPORT = "true";
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
}
