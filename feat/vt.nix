{
  services.nginx = {
    enable = true;
    virtualHosts = {
      "devvt.pcarrier.com" = {
        enableACME = true;
        forceSSL = true;
        extraConfig = ''
          ssl_buffer_size 4k;
        '';
        locations = {
          "/" = {
            proxyPass = "http://127.0.0.1:10000/";
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
      "vt.pcarrier.com" = {
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
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];
}
