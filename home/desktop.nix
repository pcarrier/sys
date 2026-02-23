{
  pkgs,
  lib,
  system,
  edl-ng,
  desktop,
}:
let
  clip = pkgs.stdenv.mkDerivation {
    name = "clip";
    src = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/sentriz/cliphist/refs/heads/master/contrib/cliphist-fuzzel-img";
      sha256 = "sha256-NgQ87yZCusF/FYprJJ+fvkA3VdrvHp4LyylQ0ajBvjU=";
    };
    phases = [ "installPhase" ];
    installPhase = ''
      install -Dm755 $src $out/bin/clip
    '';
  };
in
lib.mkIf desktop {
  home.packages = with pkgs; [
    clip
    code-cursor
    edl-ng.packages.${system}.default
    element-desktop
    legcord
    networkmanagerapplet
    pcmanfm-qt
    signal-desktop
    spotify
    xwayland-satellite
    wayfarer
    wl-clipboard-rs
    zoom-us
  ];
  programs = {
    alacritty = {
      enable = true;
      theme = "tomorrow_night_bright";
      settings = {
        font = {
          normal.family = "PragmataPro Mono Liga";
          size = 8;
        };
      };
    };
    fuzzel = {
      enable = true;
      settings = {
        main = {
          dpi-aware = false;
          font = "PragmataPro Mono Liga:size=8";
          show-actions = true;
          horizontal-pad = 0;
          vertical-pad = 0;
        };
        border.radius = 0;
        colors = {
          background = "000000a0";
          border = "ff0000a0";
          input = "ffffffff";
          prompt = "ffffffff";
          selection = "ff0000ff";
          selection-match = "ffffffff";
          selection-text = "000000ff";
          text = "ffffffff";
        };
      };
    };
    swaylock = {
      enable = true;
      settings = {
        color = "000000";
      };
    };
    waybar = {
      enable = true;
      style = ''
        * {
          font-family: "PragmataPro Liga";
          font-size: 8pt;
        }
      '';
      settings = {
        mainBar = {
          spacing = 16;
          modules-left = [
            "tray"
            "cpu"
            "memory"
            "temperature"
          ];
          modules-center = [ "niri/window" ];
          modules-right = [
            "network"
            "wireplumber"
            "battery"
            "clock"
          ];
          clock.format = "{:%F %H:%M}";
          network.format = "{essid} {signaldBm}";
        };
      };
    };
  };
  services = {
    cliphist.enable = true;
    playerctld.enable = true;
    swaync = {
      enable = true;
      settings = {
        widgets = [
          "volume"
          "mpris"
          "title"
          "dnd"
          "notifications"
        ];
      };
    };
  };
  xdg = {
    portal = {
      enable = true;
      config.common = {
        default = "gtk";
        "org.freedesktop.impl.portal.Screenshot" = "gnome";
        "org.freedesktop.impl.portal.ScreenCast" = "gnome";
      };
      extraPortals = with pkgs; [
        xdg-desktop-portal-gtk
        xdg-desktop-portal-gnome
      ];
    };
    configFile = {
      "niri/config.kdl".source = ./dotfiles/niri.kdl;
    };
  };
  home.pointerCursor = {
    enable = true;
    package = (pkgs.fuchsia-cursor.override { themeVariants = [ "Fuchsia-Red" ]; });
    name = "Fuchsia-Red";
    size = 24;
  };
}
