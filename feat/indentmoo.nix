{
  pkgs,
  moo,
  system,
  ...
}:
{
  networking.firewall.allowedTCPPorts = [
    80
    443
  ];

  systemd.services.moo = {
    description = "moo agent harness";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      ExecStart = "${pkgs.fish}/bin/fish -l -c '${moo.packages.${system}.default}/bin/moo serve --port 7777 --host 127.0.0.1'";
      WorkingDirectory = "/src";
      User = "pcarrier";
      Group = "users";
      Restart = "on-failure";
    };
  };

  services.nginx = {
    enable = true;
    virtualHosts."moo.pierre.dev.indent.sh" = {
      enableACME = true;
      forceSSL = true;
      locations."/" = {
        proxyPass = "http://127.0.0.1:7777/";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_buffering off;
          proxy_request_buffering off;
        '';
      };
    };
  };
}
