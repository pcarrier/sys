{
  pkgs,
  lib,
  system,
  edl-ng,
  claude-desktop,
  codex-desktop,
  llm-agents,
  desktop,
}:
let
  executor = llm-agents.packages.${system}.executor;
  wsConfig = pkgs.writeText "ws.yaml" ''
    version: "0.5"
    processes:
      brave:
        command: ${pkgs.brave}/bin/brave
        availability:
          restart: always
      claude-desktop:
        command: ${claude-desktop.packages.${system}.default}/bin/claude-desktop
        availability:
          restart: always
      slack:
        command: ${pkgs.slack}/bin/slack
        availability:
          restart: always
  '';
  ws = pkgs.writeShellScriptBin "ws" ''
    exec ${pkgs.process-compose}/bin/process-compose up -f ${wsConfig} "$@"
  '';
  clip = pkgs.stdenv.mkDerivation {
    name = "clip";
    src = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/sentriz/cliphist/refs/heads/master/contrib/cliphist-fuzzel-img";
      sha256 = "sha256-EtaxS5QQMU1j0Izj4W4jLU7eYadPwV9Xdu3tx+O9sNE=";
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
    executor
    ws
    claude-desktop.packages.${system}.default
    codex-desktop.packages.${system}.default
    edl-ng.packages.${system}.default
    llm-agents.packages.${system}.zcode
    legcord
    networkmanagerapplet
    pcmanfm-qt
    qutebrowser
    signal-desktop
    spotify
    vlc
    xwayland-satellite
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
  systemd.user.services."sh.executor.daemon" = {
    Unit.Description = "Executor MCP gateway";
    Service = {
      ExecStartPre = "${pkgs.coreutils}/bin/mkdir -p %h/.executor";
      ExecStart = "${lib.getExe executor} daemon run --foreground --port 4789 --hostname 127.0.0.1";
      WorkingDirectory = "%h";
      Environment = [
        "EXECUTOR_SUPERVISED=1"
        "EXECUTOR_DATA_DIR=%h/.executor"
        "EXECUTOR_SCOPE_DIR=%h/.executor"
        "EXECUTOR_SERVICE_VERSION=${executor.version}"
        "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
      ];
      Restart = "on-failure";
      RestartSec = 5;
    };
    Install.WantedBy = [ "default.target" ];
  };
  xdg = {
    desktopEntries.executor = {
      name = "Executor";
      comment = "Manage MCP integrations and connections";
      exec = "${lib.getExe executor} open";
      icon = "applications-development";
      categories = [ "Development" ];
      terminal = false;
    };
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
