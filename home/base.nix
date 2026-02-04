{
  pkgs,
  system,
  baze,
  nix-index,
  plenty,
}:
{
  home = {
    stateVersion = "25.11";
    username = "pcarrier";
    homeDirectory = "/home/pcarrier";
    packages = with pkgs; [
      bat
      baze.packages.${system}.default
      bubblewrap
      dconf
      dive
      fd
      fastfetch
      ffmpeg
      file
      fio
      flutter
      htop
      gnuplot
      jo
      jq
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
      nix-index.packages.${system}.default
      nixos-shell
      nixfmt
      nmap
      perf
      plenty.packages.${system}.plenty
      procs
      pssh
      rclone
      ripgrep
      ookla-speedtest
      slipshow
      sshfs
      sysstat
      tk
      tokei
      tree
      yt-dlp
      zoxide
    ];
    sessionVariables.ZED_WINDOW_DECORATIONS = "server";
  };
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
}
