{ moo, system, ... }:
let
  port = 7777;
  host = "127.0.0.1";
in
{
  services.nginx.virtualHosts."mymoo.pcarrier.com" = {
    enableACME = true;
    forceSSL = true;
    locations."/" = {
      proxyPass = "http://${host}:${toString port}/";
      proxyWebsockets = true;
      extraConfig = ''
        proxy_buffering off;
        proxy_request_buffering off;
      '';
    };
  };

  systemd.services.moo = {
    description = "Moo";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      HOME = "/home/pcarrier";
      HOST = host;
      PORT = toString port;
    };
    serviceConfig = {
      Type = "simple";
      User = "pcarrier";
      WorkingDirectory = "/home/pcarrier";
      ExecStart = "${moo.packages.${system}.default}/bin/moo";
      Restart = "always";
      RestartSec = "1s";
    };
  };
}
