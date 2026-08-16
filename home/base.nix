{
  pkgs,
  lib,
  system,
  systemType,
  baze,
  blit,
  plenty,
}:
lib.mkMerge [
  {
    programs.man.generateCaches = false;
    home = {
      enableNixpkgsReleaseCheck = false;
      stateVersion = "26.11";
      username = "pcarrier";
      homeDirectory = if pkgs.stdenv.isDarwin then "/Users/pcarrier" else "/home/pcarrier";
      packages =
        with pkgs;
        [
          asciinema
          asciinema-agg
          bat
          baze.packages.${system}.default
          blit.packages.${system}.default
          code-cursor
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
          raycast
        ];
      sessionVariables = {
        OLLAMA_HOST = "dog";
        ZED_WINDOW_DECORATIONS = "server";
      };
    };
    manual.manpages.enable = false;
    programs.mpv.enable = true;
  }
  (
    {
      wsl = {
        home = {
          sessionVariables = {
            EDITOR = "${pkgs.neovim}/bin/nvim";
            VISUAL = "${pkgs.neovim}/bin/nvim";
          };
        };
      };
      bare = {
        home = {
          packages = with pkgs; [
            blueman
            brave
            brightnessctl
            noto-fonts-color-emoji
            coppwr
            drm_info
            firefox
            obs-studio
            lxqt.pavucontrol-qt
          ];
          sessionVariables = {
            EDITOR = "${pkgs.neovim}/bin/nvim";
            VISUAL = "${pkgs.neovim}/bin/nvim";
          };
        };
      };
      mac = {
        home.sessionVariables = {
          EDITOR = "${pkgs.neovim}/bin/nvim";
          VISUAL = "${pkgs.neovim}/bin/nvim";
        };
      };
    }
    .${systemType} or { }
  )
  (lib.mkIf pkgs.stdenv.isDarwin {
    home.packages = with pkgs; [
      aerospace
      betterdisplay
      noto-fonts-color-emoji
      slack
      steam-unwrapped
      spotify
      zoom-us
    ];
  })
  (lib.mkIf (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
    home.packages = with pkgs; [ slack ];
  })
  (lib.mkIf pkgs.stdenv.isLinux {
    dconf.settings."org/gnome/desktop/interface".color-scheme = "prefer-dark";
    # PragmataPro has no CJK coverage, so browsers (Brave/Chromium) render
    # tofu without an explicit fallback in the default font families.
    home.packages = with pkgs; [
      noto-fonts-cjk-sans
      noto-fonts-cjk-serif
    ];
    fonts.fontconfig = {
      enable = true;
      antialiasing = true;
      subpixelRendering = "none";
      hinting = "full";
      defaultFonts = {
        monospace = [
          "PragmataPro Mono Liga"
          "Noto Sans Mono CJK JP"
        ];
        sansSerif = [
          "PragmataPro Liga"
          "Noto Sans CJK JP"
        ];
        serif = [ "Noto Serif CJK JP" ];
      };
    };
    gtk = rec {
      enable = true;
      colorScheme = "dark";
      font = {
        name = "PragmataPro Liga";
        size = 8;
      };
      theme = {
        name = "Colloid-Red-Dark";
        package = pkgs.colloid-gtk-theme.override {
          themeVariants = [ "red" ];
          colorVariants = [ "dark" ];
          tweaks = [ "black" ];
        };
      };
      gtk4.theme = theme;
      iconTheme = {
        name = "Colloid-Red-Dark";
        package = pkgs.colloid-icon-theme.override {
          colorVariants = [ "red" ];
        };
      };
    };
    qt = {
      enable = true;
      platformTheme.name = "gtk3";
    };
    services.ssh-agent.enable = true;
  })
]
