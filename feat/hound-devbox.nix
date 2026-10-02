# HOUND runs the agent computer on LOCAL protected storage. The public
# Ultimator/Flower/Garage stack remains on indentbox. Never import its server
# module here or replace HOUND's existing nginx/YAS/runner services.
{ pkgs, ... }:
let
  # Exact already-running standalone YAS build copied from indentbox; keep
  # this workload isolated from HOUND's existing packaged YAS instances.
  yasIndentbox = /nix/store/ia4nnc0chixzxnqnai3hd24n8gbicqy9-yas-0.3.1;
in
{
  virtualisation.docker = {
    enable = true;
    # The migrated kind nodes already use containerd overlayfs. Do not place
    # their /var volumes on ZFS or silently switch snapshotter formats.
    storageDriver = "overlay2";
    # Keep HOUND's old inactive /var/lib/docker metadata untouched.
    daemon.settings."data-root" = "/var/lib/docker-devbox";
  };
  users.users.pcarrier = {
    extraGroups = [ "docker" ];
    linger = true;
  };

  # Verified directly from indentbox's public host key. Preserve the user's
  # old known_hosts; migration/deployment SSH uses this separate strict pin.
  environment.etc."devbox/indentbox-known-hosts".text = ''
    indentbox ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICkhLj+eEZfNl7v0oEzcVutN4MpHWrLFo/ElwetNYqau
  '';

  # /var is tank/var, already snapshotted and replicated. A sparse ext4 image
  # on that dataset avoids putting state on the unprotected tank/root. Image
  # creation/formatting is a separate, guarded preparation step: activation
  # must NEVER format a pre-existing image or a real device.
  fileSystems = {
    "/var/lib/docker-devbox" = {
      device = "/var/lib/devbox/docker-data.ext4";
      fsType = "ext4";
      options = [
        "loop"
        "nofail"
      ];
      depends = [ "/var/lib/devbox" ];
      noCheck = true;
    };
    "/src/ultimator" = {
      device = "/var/lib/devbox/src/ultimator";
      fsType = "none";
      options = [
        "bind"
        "nofail"
      ];
      depends = [ "/var/lib/devbox/src/ultimator" ];
    };
    "/src/flower" = {
      device = "/var/lib/devbox/src/flower";
      fsType = "none";
      options = [
        "bind"
        "nofail"
      ];
      depends = [ "/var/lib/devbox/src/flower" ];
    };
    # HOUND's original clean /src/yas is hidden, not overwritten or deleted;
    # unmounting this bind restores it. All three Cargo siblings must match.
    "/src/yas" = {
      device = "/var/lib/devbox/src/yas";
      fsType = "none";
      options = [
        "bind"
        "nofail"
      ];
      depends = [ "/var/lib/devbox/src/yas" ];
    };
    "/src/ultimator/.dev/workspace" = {
      device = "/var/lib/devbox/workspace";
      fsType = "none";
      options = [
        "bind"
        "nofail"
      ];
      depends = [
        "/src/ultimator"
        "/var/lib/devbox/workspace"
      ];
    };
  };

  systemd.services.docker = {
    unitConfig.RequiresMountsFor = [ "/var/lib/docker-devbox" ];
    # Existing user managers keep their old supplementary groups. Grant the
    # already-authorized UID socket access without restarting their desktops
    # or all detached jobs simply to refresh Docker group membership.
    serviceConfig.ExecStartPost = [ "${pkgs.acl}/bin/setfacl -m u:pcarrier:rw /run/docker.sock" ];
  };

  # Relocated standalone YAS endpoint: preserve its original authentication
  # and default instance data, but do not change HOUND's existing YAS servers.
  systemd.services.yas-indentbox = {
    description = "Standalone YAS relocated from indentbox";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
    ];
    unitConfig.ConditionPathExists = [
      "/var/lib/devbox/yas-indentbox/ready"
      "/var/lib/devbox/yas-indentbox/yas.env"
    ];
    path = [
      yasIndentbox
      pkgs.pipewire
      pkgs.dbus
      pkgs.xwayland-satellite
    ];
    environment = {
      HOME = "/home/pcarrier";
      XDG_CONFIG_HOME = "/var/lib/devbox/yas-indentbox/config";
      XDG_STATE_HOME = "/var/lib/devbox/yas-indentbox/state";
      XDG_CACHE_HOME = "/var/lib/devbox/yas-indentbox/cache";
      XDG_DATA_HOME = "/var/lib/devbox/yas-indentbox/data";
      XDG_RUNTIME_DIR = "/run/yas-indentbox";
      DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
      YAS_SOCK = "/run/yas-indentbox/yas-default.sock";
      YAS_EDGE = "1";
      YAS_ADDR = "100.77.9.102:13264";
      YAS_TRUSTED_PROXY_IPS = "100.110.157.123,127.0.0.1";
      YAS_AUDIO = "1";
      YAS_FONT_EXPORT = "1";
      NIXOS_OZONE_WL = "1";
      ELECTRON_OZONE_PLATFORM_HINT = "wayland";
      MOZ_ENABLE_WAYLAND = "1";
      GDK_BACKEND = "wayland";
      QT_QPA_PLATFORM = "wayland";
      SDL_VIDEODRIVER = "wayland";
    };
    serviceConfig = {
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = "/home/pcarrier";
      EnvironmentFile = "/var/lib/devbox/yas-indentbox/yas.env";
      RuntimeDirectory = "yas-indentbox";
      RuntimeDirectoryMode = "0700";
      ExecStart = "${yasIndentbox}/bin/yas server --name default --socket /run/yas-indentbox/yas-default.sock";
      Restart = "on-failure";
      RestartSec = "3s";
      UMask = "0077";
      LimitNOFILE = "1048576:1048576";
      TasksMax = "infinity";
    };
  };

  systemd.services.ultimator-devbox = {
    description = "Ultimator devbox computer (local HOUND compute)";
    # Marker is created by the coordinator ONLY after source worker fencing,
    # final cold sync, and validation. It permits normal restart/boot later
    # but ensures this preparation cannot seize the live machine identity.
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    requires = [ "user@1000.service" ];
    after = [
      "network-online.target"
      "user@1000.service"
    ];
    unitConfig = {
      RequiresMountsFor = [
        "/src/ultimator/.dev/workspace"
        "/src/flower"
        "/src/yas"
        "/srv/devbox/bin"
      ];
      ConditionPathIsMountPoint = [
        "/src/ultimator"
        "/src/flower"
        "/src/yas"
        "/src/ultimator/.dev/workspace"
      ];
      ConditionPathExists = [
        "/var/lib/devbox/cutover-ready"
        "/srv/devbox/bin/ultimator"
        "/srv/devbox/yas-bin/yas"
        "/srv/devbox/computer.json"
        "/src/ultimator/flake.nix"
      ];
    };
    environment = {
      HOME = "/home/pcarrier";
      ULTIMATOR_URL = "https://ultimator.app";
      ULTIMATOR_PUBLIC_URL = "https://ultimator.app";
      ULTIMATOR_YAS_BIN = "/srv/devbox/yas-bin/yas";
      XDG_STATE_HOME = "/var/lib/devbox/state";
      XDG_CACHE_HOME = "/var/lib/devbox/cache";
      XDG_RUNTIME_DIR = "/run/user/1000";
      DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
    };
    serviceConfig = {
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = "/src/ultimator/.dev/workspace";
      # nix develop supplies the pinned toolchain without sourcing .env.local
      # or direnv's server credentials. Preserve the user's login environment.
      UnsetEnvironment = [ "FLOWER_ADMIN_TOKEN" ];
      ExecStart = "${pkgs.fish}/bin/fish --login --command 'set -e FLOWER_ADMIN_TOKEN; exec ${pkgs.nix}/bin/nix develop /src/ultimator --command /srv/devbox/bin/ultimator computer start devbox --workspace /src/ultimator/.dev/workspace --config /srv/devbox/computer.json'";
      Restart = "on-failure";
      RestartSec = "3s";
      TimeoutStopSec = "90s";
      UMask = "0077";
      Nice = 10;
      CPUWeight = 20;
      IOWeight = 20;
      LimitNOFILE = "1048576:1048576";
      TasksMax = "infinity";
    };
  };
}
