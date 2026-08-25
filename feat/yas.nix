{
  yas,
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
  imports = [ yas.nixosModules.yas ];

  networking = {
    firewall.allowedTCPPorts = [
      80
      443
    ];
  };

  # The packaged YAS server gets its RT budget from the module's unit,
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
    # One PTY master is one descriptor, and a client connection, a watched
    # file, and an extension socket are each one more, so a terminal server's
    # ceiling on live terminals is its descriptor limit long before it is
    # anything YAS declares — the protocol allows 65535 per session and
    # YAS_MAX_PTYS defaults to unlimited. systemd hands a service 1024 soft
    # (see the unit below) and a login shell whatever this says.
    #
    # Not `unlimited`: fs.nr_open is 2147483584 here, and a soft limit that
    # large is inherited by every spawned shell, where the occasional program
    # that sizes an array or a close loop by RLIMIT_NOFILE turns it into a
    # startup stall. A million descriptors is past any real terminal count
    # and stays inside what such code handles.
    {
      domain = "@users";
      type = "-";
      item = "nofile";
      value = "1048576";
    }
    {
      domain = "@users";
      type = "-";
      item = "nproc";
      value = "unlimited";
    }
  ];

  # kernel.pty.max is the hard ceiling on Unix98 PTYs for the whole machine,
  # and its 4096 default is the first thing a many-terminal session hits: it
  # is shared with every other terminal emulator, ssh session, and container
  # on the host, and the allocation failure surfaces as a CREATE refusal with
  # nothing in the server's own accounting to explain it. 1048576 is
  # NR_UNIX98_PTY_MAX, the largest the kernel accepts.
  boot.kernel.sysctl = {
    "kernel.pty.max" = 1048576;
    # A terminal that opens an editor opens inotify watches; the default
    # instance count is per-user, not per-process, so a few hundred live
    # terminals exhaust it and file watching fails silently in each one.
    "fs.inotify.max_user_instances" = 1048576;
    "fs.inotify.max_user_watches" = 1048576;
  };

  services.nginx = {
    enable = true;
    recommendedProxySettings = true;
    virtualHosts = {
      "yasdev.pcarrier.com" = {
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
            '';
          };
        };
      };
      "yas.pcarrier.com" = {
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

  # YAS is a Wayland-only compositor (no XWayland), so GUI apps launched in a
  # YAS terminal must use their Wayland backends — otherwise X11-default apps
  # (Electron/Cursor, Firefox, GTK, Qt) come up with no window. PTY shells
  # inherit the server service env, and scoping these here (rather than
  # global sessionVariables) keeps them out of the host's other, XWayland-
  # capable sessions (sway/gnome/plasma).
  systemd.services = lib.genAttrs (map (u: "yas-server@${u}") normalUsers) (_: {
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
    # The module's unit sets no descriptor limit, so the server runs at
    # systemd's 1024 soft default while its hard limit sits at 524288 — and
    # the server only ever reads the hard one (to bound the cloexec sweep it
    # does before exec), never raising the soft one for itself. Every PTY
    # master, client socket, watched file, and extension connection spends
    # from that 1024. Soft is set equal to hard because nothing raises it
    # later; the value matches the login-shell limit above so a dev stack and
    # the packaged server hit the same ceiling.
    #
    # TasksMax is the other per-unit ceiling worth removing: the whole
    # process tree of every terminal lives in this one cgroup, so the default
    # (153484 here, from DefaultTasksMax) is shared by every shell, editor,
    # language server, and build those terminals start.
    serviceConfig = {
      LimitNOFILE = "1048576:1048576";
      LimitNPROC = "infinity";
      TasksMax = "infinity";
    };
  });

  services.yas = {
    enable = true;
    audio.enable = true;
    users = normalUsers;
    # Keyed by the user whose server serves them: the edge and the share run
    # inside that server rather than as units of their own, so the browser and
    # every WebRTC consumer reach the terminals without a socket in between.
    edges.pcarrier = {
      port = 3264;
      passFile = "/etc/yas.env";
    };
    shares.pcarrier = {
      passFile = "/etc/yas.env";
      verboseWebrtc = true;
    };
  };
}
