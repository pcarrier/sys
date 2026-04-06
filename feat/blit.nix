{
  blit,
  pkgs,
  system,
  lib,
  config,
  ...
}:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  imports = [ blit.nixosModules.blit ];

  networking = {
    firewall.allowedTCPPorts = [
      80
      443
    ];
    firewall.allowedUDPPorts = [
      443
      3264
    ];
    nftables = {
      enable = true;
      tables.blit-redirect = {
        family = "inet";
        content = ''
          chain prerouting {
            type nat hook prerouting priority dstnat; policy accept;
            udp dport 443 redirect to :3264
          }
        '';
      };
    };
  };

  services.nginx = {
    enable = true;
    virtualHosts = {
      "blitdev.pcarrier.com" = {
        enableACME = true;
        forceSSL = true;
        extraConfig = ''
          ssl_buffer_size 4k;
        '';
        locations = {
          "/" = {
            proxyPass = "http://hound:10000/";
            proxyWebsockets = true;
            extraConfig = ''
              proxy_buffering off;
              proxy_request_buffering off;
              tcp_nodelay on;
              add_header Alt-Svc 'h3=":443"; ma=86400' always;
            '';
          };
        };
      };
      "blit.pcarrier.com" = {
        enableACME = true;
        forceSSL = true;
        extraConfig = ''
          ssl_buffer_size 4k;
        '';
        locations = {
          "/" = {
            proxyPass = "http://127.0.0.1:3264/";
            proxyWebsockets = true;
            extraConfig = ''
              proxy_buffering off;
              proxy_request_buffering off;
              tcp_nodelay on;
              add_header Alt-Svc 'h3=":443"; ma=86400' always;
            '';
          };
        };
      };
    };
  };

  home-manager.users.pcarrier.home.packages = with pkgs; [
    chromium
    noto-fonts-color-emoji
  ];

  services.blit = {
    enable = true;
    users = normalUsers;
    gateways.pcarrier = {
      user = "pcarrier";
      port = 3264;
      passFile = "/etc/blit.env";
      storeConfig = true;
    };
    forwarders.pcarrier = {
      user = "pcarrier";
      passFile = "/etc/blit.env";
    };
  };
}
