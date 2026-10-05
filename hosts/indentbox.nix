{ lib }:
lib.bare {
  name = "indentbox";
  trusted = true;
  desktop = true;
  system = "x86_64-linux";
  hardware = ../hw/indentbox.nix;
  extraModules = [
    ../feat/sandbox-ssh.nix
    ../feat/nvidia.nix
    ../feat/waydroid.nix
    ../feat/ultimator.nix
    ../feat/ultimator-cd.nix
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
          # Behind Caddy (feat/ultimator.nix), which logs them to its access
          # log (`import logged`, `logFormat = null`) and redirects port 80.
          caddy.virtualHosts = {
            "yas.pierre.dev.indent.sh" = {
              hostName = "https://yas.pierre.dev.indent.sh";
              useACMEHost = "yas.pierre.dev.indent.sh";
              logFormat = null;
              extraConfig = ''
                import logged
                reverse_proxy 127.0.0.1:3264 {
                  flush_interval -1
                }
              '';
            };
            # Dev edge (YAS built from source, run by hand on :10000).
            # Mirrors yasdev.pcarrier.com in feat/yas.nix.
            "yasdev.pierre.dev.indent.sh" = {
              hostName = "https://yasdev.pierre.dev.indent.sh";
              useACMEHost = "yasdev.pierre.dev.indent.sh";
              logFormat = null;
              extraConfig = ''
                import logged
                reverse_proxy 127.0.0.1:10000 {
                  flush_interval -1
                }
              '';
            };
          };
          tailscale.enable = true;
        };
        # HTTP-01 through the webroot Caddy serves on port 80.
        security.acme.certs = {
          "yas.pierre.dev.indent.sh".webroot = "/var/lib/acme/acme-challenge";
          "yasdev.pierre.dev.indent.sh".webroot = "/var/lib/acme/acme-challenge";
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
        # YAS's own WebTransport, on its own port. UDP 443 used to be
        # redirected here (nftables, prerouting and output); since 10-01 it is
        # Caddy's HTTP/3 (feat/ultimator.nix).
        networking.firewall.allowedUDPPorts = [
          10001
        ];
        networking.nftables = {
          enable = true;
          # The redirect's table, gone from `tables`, which only cleans up the
          # tables it still lists.
          extraDeletions = ''
            table inet yas-webtransport-redirect;
            delete table inet yas-webtransport-redirect;
          '';
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
