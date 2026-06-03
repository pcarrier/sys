{ lib }:
lib.ec2 {
  name = "indentbox";
  system = "aarch64-linux";
  extraModules = [
    ../feat/indentmoo.nix
    ../feat/indentcode.nix
    (
      { blit, ... }:
      {
        imports = [ blit.nixosModules.blit ];
        services = {
          blit = {
            enable = true;
            users = [ "pcarrier" ];
            gateways.pcarrier = {
              user = "pcarrier";
              port = 3264;
              passFile = "/etc/blit.env";
              storeConfig = true;
              webrtcProxy = true;
              quic = true;
            };
          };
          nginx.virtualHosts."blit.pierre.dev.indent.sh" = {
            enableACME = true;
            forceSSL = true;
            locations."/" = {
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
          tailscale.enable = true;
        };
        hardware.graphics.enable = true;

        # blit is a Wayland-only compositor (no XWayland), so GUI apps launched
        # in a blit terminal must use their Wayland backends — otherwise
        # X11-default apps (Electron/Cursor, Firefox, GTK, Qt) come up with no
        # window. PTY shells inherit the blit-server service env. Scoped to the
        # service so it stays out of any other (XWayland-capable) sessions.
        # Mirrors feat/blit.nix.
        systemd.services."blit-server@pcarrier".environment = {
          NIXOS_OZONE_WL = "1";
          ELECTRON_OZONE_PLATFORM_HINT = "wayland";
          MOZ_ENABLE_WAYLAND = "1";
          GDK_BACKEND = "wayland";
          QT_QPA_PLATFORM = "wayland";
          SDL_VIDEODRIVER = "wayland";
        };

        # Browser WebTransport (QUIC) arrives on UDP/443; dstnat-redirect it to
        # the blit gateway's QUIC listener on :3264 so datagrams actually reach
        # it. QUIC is end-to-end to the gateway (it pins its own self-signed
        # cert via serverCertificateHashes) — this is a dumb port redirect, no
        # TLS termination. Without it the gateway advertises WebTransport
        # (wt=<hash>) but datagrams never arrive, so every page (re)load waits
        # out the browser's WT connect timeout before falling back to WebSocket.
        # Mirrors feat/blit.nix.
        networking.firewall.allowedUDPPorts = [
          443
          3264
        ];
        networking.nftables = {
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
