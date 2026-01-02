{ pkgs, ... }:
{
  services = {
    nzbget = {
      enable = true;
      user = "pcarrier";
    };
    plex = {
      enable = true;
      openFirewall = true;
    };
    samba = {
      enable = true;
      settings = {
        tank = {
          path = "/tank";
          readOnly = false;
        };
        tonk = {
          path = "/tonk";
          readOnly = false;
        };
      };
    };
    sonarr.enable = true;
    tautulli.enable = true;
    immich = {
      enable = true;
      port = 2283;
      mediaLocation = "/tank/immich";
    };
    nginx = {
      enable = true;
      virtualHosts = {
        "photos.pcarrier.com" = {
          enableACME = true;
          forceSSL = true;
          locations = {
            "/" = {
              proxyPass = "http://[::1]:2283/";
              proxyWebsockets = true;
              extraConfig = ''
                client_max_body_size 30000M;
                proxy_read_timeout   600s;
                proxy_send_timeout   600s;
                send_timeout         600s;
              '';
            };
          };
        };
        "16al.pcarrier.com" = {
          enableACME = true;
          forceSSL = true;
          locations = {
            "/" = {
              proxyPass = "http://127.0.0.1:6789/";
            };
          };
        };
        "sonarr.pcarrier.com" = {
          enableACME = true;
          forceSSL = true;
          locations = {
            "/" = {
              proxyPass = "http://127.0.0.1:8989/";
            };
          };
        };
        "tautulli.pcarrier.com" = {
          enableACME = true;
          forceSSL = true;
          locations = {
            "/" = {
              proxyPass = "http://127.0.0.1:8181/";
            };
          };
        };
      };
    };
  };
  users.users = {
    immich.extraGroups = [
      "video"
      "render"
    ];
  };
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
  home-manager.users.pcarrier.home.packages = with pkgs; [
    immich-cli
    immich-go
    rtorrent
  ];
}
