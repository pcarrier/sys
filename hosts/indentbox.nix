{ lib }:
lib.bare {
  name = "indentbox";
  desktop = true;
  system = "x86_64-linux";
  hardware = ../hw/indentbox.nix;
  extraModules = [
    ../feat/nvidia.nix
    (
      { yas, ... }:
      {
        imports = [ yas.nixosModules.yas ];
        services = {
          yas = {
            enable = true;
            users = [ "pcarrier" ];
            audio.enable = true;
            edges.pcarrier = {
              port = 3264;
              passFile = "/etc/yas.env";
              trustedProxyIps = [ "127.0.0.1" ];
              webTransport = {
                enable = true;
                addr = "0.0.0.0";
                port = 3264;
                publicPort = 443;
                openFirewall = true;
              };
            };
          };
          nginx = {
            enable = true;
            recommendedProxySettings = true;
            virtualHosts = {
              "yas.pierre.dev.indent.sh" = {
                enableACME = true;
                forceSSL = true;
                locations."/" = {
                  proxyPass = "http://127.0.0.1:3264/";
                  proxyWebsockets = true;
                  extraConfig = ''
                    proxy_buffering off;
                    proxy_request_buffering off;
                    tcp_nodelay on;
                  '';
                };
              };
              # Dev edge (YAS built from source, run by hand on :10000).
              # Mirrors yasdev.pcarrier.com in feat/yas.nix.
              "yasdev.pierre.dev.indent.sh" = {
                enableACME = true;
                forceSSL = true;
                locations."/" = {
                  proxyPass = "http://127.0.0.1:10000/";
                  proxyWebsockets = true;
                  extraConfig = ''
                    proxy_buffering off;
                    proxy_request_buffering off;
                    tcp_nodelay on;
                  '';
                };
              };
            };
          };
          tailscale.enable = true;
        };
        hardware.graphics.enable = true;

        # YAS is a Wayland-only compositor (no XWayland), so GUI apps launched
        # in a YAS terminal must use their Wayland backends — otherwise
        # X11-default apps (Electron/Cursor, Firefox, GTK, Qt) come up with no
        # window. PTY shells inherit the yas-server service env. Scoped to the
        # service so it stays out of any other (XWayland-capable) sessions.
        # Mirrors feat/yas.nix.
        systemd.services."yas-server@pcarrier".environment = {
          NIXOS_OZONE_WL = "1";
          ELECTRON_OZONE_PLATFORM_HINT = "wayland";
          MOZ_ENABLE_WAYLAND = "1";
          GDK_BACKEND = "wayland";
          QT_QPA_PLATFORM = "wayland";
          SDL_VIDEODRIVER = "wayland";
        };

        networking.firewall.allowedTCPPorts = [
          80
          443
        ];
        # WebTransport is advertised on the standard HTTPS port. Keep the YAS
        # process on an unprivileged port and redirect only the UDP traffic;
        # TCP/443 continues to terminate at nginx.
        networking.firewall.allowedUDPPorts = [
          443
          10001
        ];
        networking.nftables = {
          enable = true;
          tables.yas-webtransport-redirect = {
            family = "inet";
            content = ''
              chain prerouting {
                type nat hook prerouting priority dstnat; policy accept;
                udp dport 443 redirect to :3264
              }

              # Connections originating on indentbox route its public address
              # through lo, bypassing prerouting. Redirect only local
              # destinations here so normal outbound HTTP/3 stays untouched.
              chain output {
                type nat hook output priority dstnat; policy accept;
                fib daddr type local udp dport 443 redirect to :3264
              }
            '';
          };
        };
        networking.firewall.interfaces.tailscale0 = {
          allowedTCPPortRanges = [
            {
              from = 0;
              to = 65535;
            }
          ];
          allowedUDPPortRanges = [
            {
              from = 0;
              to = 65535;
            }
          ];
        };
      }
    )
  ];
} lib.commonInputs
