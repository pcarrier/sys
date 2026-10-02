# HOUND runs the agent computer on LOCAL protected storage. The public
# Ultimator/Flower/Garage stack remains on indentbox. Never import its server
# module here or replace HOUND's existing nginx/YAS/runner services.
{ pkgs, ... }:
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
