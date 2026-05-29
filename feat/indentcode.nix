{
  pkgs,
  ...
}:
{
  # Browser VS Code via OpenVSCode Server, proxied at
  # https://code.pierre.dev.indent.sh.
  #
  # Auth is handled by nginx HTTP basic auth (basicAuthFile), NOT VS Code:
  # openvscode-server is unauthenticated on its loopback port, and nginx gates
  # everything in front of it. The htpasswd file lives OUTSIDE the Nix store at
  # /etc/code.htpasswd, matching the /etc/blit.env pattern. Create it before
  # deploying, e.g.:
  #
  #   nix run nixpkgs#apacheHttpd -- htpasswd -B -c /etc/code.htpasswd pierre
  #   chown root:nginx /etc/code.htpasswd && chmod 640 /etc/code.htpasswd
  services.openvscode-server = {
    enable = true;
    package = pkgs.openvscode-server;
    host = "127.0.0.1";
    port = 4444;
    user = "pcarrier";
    group = "users";
    withoutConnectionToken = true;
    telemetryLevel = "off";
    extraArguments = [ "/src" ];
  };

  services.nginx = {
    enable = true;
    virtualHosts."code.pierre.dev.indent.sh" = {
      enableACME = true;
      forceSSL = true;
      basicAuthFile = "/etc/code.htpasswd";
      locations."/" = {
        proxyPass = "http://127.0.0.1:4444/";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-Host $host;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_buffering off;
          proxy_request_buffering off;
        '';
      };
    };
  };
}
