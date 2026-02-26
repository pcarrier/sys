{
  pkgs,
  lib,
  system,
  systemType,
  baze,
  plenty,
}:
lib.mkMerge [
  {
    home = {
      stateVersion = "25.11";
      username = "pcarrier";
      homeDirectory = if pkgs.stdenv.isDarwin then "/Users/pcarrier" else "/home/pcarrier";
      packages =
        with pkgs;
        [
          asciinema
          asciinema-agg
          bat
          baze.packages.${system}.default
          dive
          fd
          fastfetch
          ffmpeg
          file
          flutter
          htop
          gnuplot
          jo
          jq
          k9s
          ldns
          libarchive
          lnav
          lsof
          moreutils
          mosh
          mpv
          ncdu
          nil
          nixd
          nixfmt
          nmap
          plenty.packages.${system}.plenty
          procs
          pssh
          rclone
          ripgrep
          ookla-speedtest
          slipshow
          tk
          tokei
          tree
          yt-dlp
          zoxide
        ]
        ++ lib.optionals pkgs.stdenv.isLinux [
          bubblewrap
          dconf
          fio
          nixos-shell
          perf
          sshfs
          sysstat
        ]
        ++ lib.optionals pkgs.stdenv.isDarwin [
          iterm2
          raycast
        ];
      sessionVariables = {
        OLLAMA_HOST = "dog";
        ZED_WINDOW_DECORATIONS = "server";
      };
    };
  }
  (
    {
      wsl = {
        home = {
          packages = with pkgs; [ wslu ];
          sessionVariables = {
            BROWSER = "wslview";
            EDITOR = "nvim";
            VISUAL = "nvim";
          };
        };
      };
      bare = {
        home = {
          packages = with pkgs; [
            blueman
            brave
            brightnessctl
            coppwr
            drm_info
            firefox
            obs-studio
            lxqt.pavucontrol-qt
          ];
          sessionVariables = {
            BROWSER = "brave";
            EDITOR = "zeditor --wait";
            VISUAL = "zeditor --wait";
          };
        };
      };
      mac = {
        home.sessionVariables = {
          EDITOR = "zeditor --wait";
          VISUAL = "zeditor --wait";
        };
      };
    }
    .${systemType}
    or { }
  )
  (lib.mkIf pkgs.stdenv.isDarwin {
    home.packages = with pkgs; [
      aerospace
      betterdisplay
      brave
      discord
      istat-menus
      ollama
      slack
      steam-unwrapped
      spotify
      zoom-us
    ];
  })
  (lib.mkIf pkgs.stdenv.isLinux {
    dconf.settings."org/gnome/desktop/interface".color-scheme = "prefer-dark";
    fonts.fontconfig = {
      enable = true;
      antialiasing = true;
      subpixelRendering = "none";
      hinting = "full";
      defaultFonts = {
        monospace = [ "PragmataPro Mono Liga" ];
        sansSerif = [ "PragmataPro Liga" ];
      };
    };
    gtk = {
      enable = true;
      colorScheme = "dark";
      font = {
        name = "PragmataPro Liga";
        size = 8;
      };
      theme = {
        name = "Flat-Remix-GTK-Red-Darkest-Solid";
        package = pkgs.flat-remix-gtk;
      };
      iconTheme = {
        name = "Flat-Remix-Red-Dark";
        package = pkgs.flat-remix-icon-theme;
      };
    };
    qt = {
      enable = true;
      platformTheme.name = "gtk3";
    };
    services.ssh-agent.enable = true;
  })
]
