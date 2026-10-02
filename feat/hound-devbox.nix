# HOUND runs the agent computer; the checkouts and all server services stay on
# indentbox. Do not import feat/ultimator.nix here: this is only the computer.
{ pkgs, ... }:
let
  # Ignore root's SSH config, and require the independently verified server key.
  # This wrapper also gives the mount helper an absolute, closure-owned ssh.
  mountSsh = pkgs.writeShellScript "devbox-mount-ssh" ''
    exec ${pkgs.openssh}/bin/ssh -F /dev/null "$@"
  '';
  sshfsOptions = [
    "_netdev"
    # An unreachable development checkout must not hold up HOUND's own servers.
    # The computer separately RequiresMountsFor all four mounts, so it fails
    # closed rather than running against the directories under a failed mount.
    "nofail"
    "nodev"
    "nosuid"
    "allow_other"
    "default_permissions"
    # Both hosts use UID 1000 / GID 100. Keep *all* numeric ownership unchanged;
    # uid=1000 or idmap=user on a root mount would widen or remap permissions.
    "idmap=none"
    "reconnect"
    "ServerAliveInterval=15"
    "ServerAliveCountMax=3"
    "ConnectTimeout=15"
    "BatchMode=yes"
    "IdentitiesOnly=yes"
    "IdentityFile=/home/pcarrier/.ssh/id_ed25519"
    "StrictHostKeyChecking=yes"
    "UserKnownHostsFile=/etc/devbox/indentbox-known-hosts"
    "GlobalKnownHostsFile=/dev/null"
    "ssh_command=${mountSsh}"
    # Git is also used on indentbox. Do not serve cached names, metadata or file
    # contents to the other writer. Do not use workaround=rename: it unlinks the
    # destination before renaming, losing Git's atomic lockfile publication.
    "dir_cache=no"
    "attr_timeout=0"
    "entry_timeout=0"
    "negative_timeout=0"
    "direct_io"
    # Preserve the checkouts' absolute and parent-relative symlink semantics.
    "no_contain_symlinks"
    "x-systemd.requires=tailscaled.service"
    "x-systemd.mount-timeout=60s"
  ];
  remoteCheckout = path: {
    device = "pcarrier@indentbox:${path}";
    fsType = "fuse.sshfs";
    options = sshfsOptions;
    depends = [ "/home/pcarrier/.ssh" ];
    noCheck = true;
  };
in
{
  virtualisation.docker.enable = true;
  users.users.pcarrier = {
    extraGroups = [ "docker" ];
    # Keep the user runtime directory and D-Bus alive when the last login exits.
    linger = true;
  };

  # Verified directly from indentbox's /etc/ssh/ssh_host_ed25519_key.pub.
  # Fingerprint: SHA256:oKXjzvnTTLzihricbfK9hLDRFej1HYKyPMZO9OKZhJc
  # Leave the user's stale known_hosts untouched; never accept a replacement key.
  environment.etc."devbox/indentbox-known-hosts".text = ''
    indentbox ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICkhLj+eEZfNl7v0oEzcVutN4MpHWrLFo/ElwetNYqau
  '';

  fileSystems = {
    "/src/ultimator" = remoteCheckout "/src/ultimator";
    "/src/flower" = remoteCheckout "/src/flower";
    # HOUND's existing /src/yas stays untouched until its path compatibility is
    # resolved by the migration coordinator.
    "/srv/devbox/yas-source" = remoteCheckout "/src/yas";
    "/src/ultimator/.dev/workspace" = {
      device = "/srv/devbox/workspace";
      fsType = "none";
      options = [ "bind" "_netdev" "nofail" ];
      # NixOS emits x-systemd.requires-mounts-for for these. The remote parent
      # must be mounted first or it would hide the local workspace bind later.
      depends = [ "/src/ultimator" "/srv/devbox/workspace" ];
    };
  };

  systemd.services.ultimator-devbox = {
    description = "Ultimator devbox computer (coordinator-controlled handoff)";
    # Intentionally static: validation/switch must not start or enable it. The
    # coordinator explicitly starts it after handing over the existing identity.
    wantedBy = [ ];
    wants = [ "network-online.target" ];
    requires = [ "user@1000.service" ];
    after = [ "network-online.target" "user@1000.service" ];
    unitConfig = {
      RequiresMountsFor = [
        "/src/ultimator/.dev/workspace"
        "/src/flower"
        "/srv/devbox/yas-source"
        "/srv/devbox/bin"
        "/srv/devbox/yas-bin"
      ];
      ConditionPathIsMountPoint = [
        "/src/ultimator"
        "/src/flower"
        "/src/ultimator/.dev/workspace"
        "/srv/devbox/yas-source"
      ];
      ConditionPathExists = [
        "/srv/devbox/bin/ultimator"
        "/srv/devbox/yas-bin/yas"
        "/srv/devbox/computer.json"
      ];
    };
    environment = {
      HOME = "/home/pcarrier";
      ULTIMATOR_URL = "https://ultimator.app";
      ULTIMATOR_YAS_BIN = "/srv/devbox/yas-bin/yas";
      XDG_RUNTIME_DIR = "/run/user/1000";
      DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
    };
    serviceConfig = {
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = "/src/ultimator/.dev/workspace";
      # No EnvironmentFile and no server .env.local. Clear the Flower admin
      # token at systemd exec time and again after noninteractive fish login.
      UnsetEnvironment = [ "FLOWER_ADMIN_TOKEN" ];
      ExecStart = "${pkgs.fish}/bin/fish --login --command 'set -e FLOWER_ADMIN_TOKEN; exec /srv/devbox/bin/ultimator computer start devbox --workspace /src/ultimator/.dev/workspace --config /srv/devbox/computer.json'";
      Restart = "on-failure";
      RestartSec = "3s";
      TimeoutStopSec = "90s";
      UMask = "0077";
      LimitNOFILE = "1048576:1048576";
      TasksMax = "infinity";
    };
  };
}
