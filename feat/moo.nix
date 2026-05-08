{
  services.nginx.virtualHosts."mymoo.pcarrier.com" = {
    enableACME = true;
    forceSSL = true;
    locations."/" = {
      proxyPass = "http://hound:5173/";
      proxyWebsockets = true;
      extraConfig = ''
        proxy_buffering off;
        proxy_request_buffering off;
      '';
    };
  };
}
