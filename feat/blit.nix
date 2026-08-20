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
      10001
    ];
    nftables = {
      enable = true;
      tables.blit-redirect = {
        family = "inet";
        content = ''
          chain prerouting {
            type nat hook prerouting priority dstnat; policy accept;
            udp dport 443 redirect to :10001
          }
        '';
      };
    };
  };

  # The packaged blit-server gets its RT budget from the blit module's unit,
  # but a dev stack started from a login shell inherits these instead — and
  # with both at 0 PipeWire's graph thread stays SCHED_OTHER, misses its
  # 21 ms cycle under video-encode load, and emits audio in bursts.
  security.pam.loginLimits = [
    {
      domain = "@users";
      type = "-";
      item = "rtprio";
      value = "95";
    }
    {
      domain = "@users";
      type = "-";
      item = "nice";
      value = "-11";
    }
  ];

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

  # blit is a Wayland-only compositor (no XWayland), so GUI apps launched in a
  # blit terminal must use their Wayland backends — otherwise X11-default apps
  # (Electron/Cursor, Firefox, GTK, Qt) come up with no window. PTY shells
  # inherit the blit-server service env, and scoping these here (rather than
  # global sessionVariables) keeps them out of the host's other, XWayland-
  # capable sessions (sway/gnome/plasma).
  systemd.services = lib.genAttrs (map (u: "blit-server@${u}") normalUsers) (_: {
    environment = {
      NIXOS_OZONE_WL = "1";
      ELECTRON_OZONE_PLATFORM_HINT = "wayland";
      # The Codex Desktop launcher otherwise disables GPU compositing on every
      # Wayland session, forcing full-rate wl_shm uploads through the compositor.
      CODEX_ELECTRON_DISABLE_GPU_COMPOSITING = "0";
      MOZ_ENABLE_WAYLAND = "1";
      GDK_BACKEND = "wayland";
      QT_QPA_PLATFORM = "wayland";
      SDL_VIDEODRIVER = "wayland";
    };
  });

  services.blit = {
    enable = true;
    audio.enable = true;
    users = normalUsers;
    gateways.pcarrier = {
      user = "pcarrier";
      port = 3264;
      passFile = "/etc/blit.env";
      storeConfig = true;
      # quic = true; <--- see https://github.com/indent-com/blit/pull/165
    };
    shares.pcarrier = {
      user = "pcarrier";
      passFile = "/etc/blit.env";
      verboseWebrtc = true;
    };
  };
}
