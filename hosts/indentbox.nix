{ lib }:
lib.bare {
  name = "indentbox";
  trusted = true;
  desktop = true;
  system = "x86_64-linux";
  hardware = ../hw/indentbox.nix;
  extraModules = [
    ../feat/nvidia.nix
    ../feat/waydroid.nix
    ../feat/ultimator.nix
    (
      { yas, llm-agents, ... }:
      {
        imports = [ yas.nixosModules.yas ];
        environment.systemPackages = [ llm-agents.packages.x86_64-linux.hermes-agent ];
        services = {
          yas = {
            enable = true;
            users = [ "pcarrier" ];
            audio.enable = true;
            edges.pcarrier = {
              port = 3264;
              passFile = "/etc/yas.env";
              trustedProxyIps = [ "127.0.0.1" ];
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

        # BBR paces over fq and keeps its window across idle gaps, so pages
        # opened from far away don't climb back up from slow start after
        # each pause between requests.
        boot.kernelModules = [ "tcp_bbr" ];
        boot.kernel.sysctl = {
          "net.ipv4.ip_forward" = 1;
          "net.ipv6.conf.all.forwarding" = 1;
          "net.core.default_qdisc" = "fq";
          "net.ipv4.tcp_congestion_control" = "bbr";
          "net.ipv4.tcp_slow_start_after_idle" = 0;
          # Writeback starts at 128 MB of dirty pages and writers wait past 1 GB, rather than at 10% and 20%
          # of the memory free for them (some 3 and 6 GB): builds here write gigabytes at once, and flushing
          # them in bursts held Flower's fsyncs (every Ultimator mutation waits on one) for 0.3 to 2.5 s.
          "vm.dirty_background_bytes" = 134217728;
          "vm.dirty_bytes" = 1073741824;
        };
        # Builds free and rewrite gigabytes a day on a disk kept nearly full: tell the SSDs daily, not weekly.
        services.fstrim.interval = "daily";

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
                udp dport 443 redirect to :10001
              }

              # Connections originating on indentbox route its public address
              # through lo, bypassing prerouting. Redirect only local
              # destinations here so normal outbound HTTP/3 stays untouched.
              chain output {
                type nat hook output priority dstnat; policy accept;
                fib daddr type local udp dport 443 redirect to :10001
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
