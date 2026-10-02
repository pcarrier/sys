# HOUND runs the agent computer on LOCAL protected storage. The public
# Ultimator/Flower/Garage stack remains on indentbox. Never import its server
# module here or replace HOUND's existing nginx/YAS/runner services.
{
  pkgs,
  lib,
  config,
  ...
}:
let
  # The running hound kernel uses nftables, not the removed legacy xtables.
  # Keep Waydroid's rule semantics, changing only its preferred two binaries.
  # Enforce executable paths AFTER fish login and nix develop, which otherwise
  # normalize PATH and drop systemd's service.path. No global shell changes.
  devboxStart = pkgs.writeShellScript "start-devbox-local-compute" ''
    export PATH=${pkgs.lib.makeBinPath [ pkgs.pipewire pkgs.wireplumber pkgs.dbus pkgs.xwayland-satellite ]}:$PATH
    exec /srv/devbox/bin/ultimator computer start devbox --workspace /src/ultimator/.dev/workspace --config /srv/devbox/computer.json
  '';
  waydroidNetScript = "${pkgs.waydroid}/lib/waydroid/data/scripts/.waydroid-net.sh-wrapped";
  waydroidNftCompat = pkgs.writeTextFile {
    name = "waydroid-net-hound-nft-compat.sh";
    executable = true;
    text = builtins.replaceStrings
      [ "command -v iptables-legacy" "command -v ip6tables-legacy" ]
      [ "command -v iptables-nft" "command -v ip6tables-nft" ]
      (builtins.readFile waydroidNetScript);
  };
  # Root-only, child-only, lifetime-scoped delegation for these eight jobs.
  # The module's independent stop hooks otherwise revoke a paired sender.
  migrationSyncoidDelegation = pkgs.writeShellScript "migration-syncoid-delegation" ''
    export PATH=${
      lib.makeBinPath [
        pkgs.coreutils
        pkgs.util-linux
      ]
    }
    set -euo pipefail
    [ "$#" -eq 2 ] || exit 64
    action=$1
    job=$2
    case "$job" in
      migration-evidence-to-tonk) source='tank/var/devbox-migration-evidence-20261002'; target='tonk/backups/var/devbox-migration-evidence-20261002' ;;
      migration-evidence-to-tunk) source='tank/var/devbox-migration-evidence-20261002'; target="" ;;
      waydroid-stage-to-tonk) source='tank/var/devbox-waydroid-stage-20261002T082100Z'; target='tonk/backups/var/devbox-waydroid-stage-20261002T082100Z' ;;
      waydroid-stage-to-tunk) source='tank/var/devbox-waydroid-stage-20261002T082100Z'; target="" ;;
      waydroid-system-to-tonk) source='tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; target='tonk/backups/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z' ;;
      waydroid-system-to-tunk) source='tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; target="" ;;
      waydroid-user-to-tonk) source='tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; target='tonk/backups/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z' ;;
      waydroid-user-to-tunk) source='tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; target="" ;;
      *) echo 'Refusing non-migration job' >&2; exit 64 ;;
    esac
    case "$action" in enter|leave) ;; *) exit 64 ;; esac
    zfs=/run/booted-system/sw/bin/zfs
    state=/run/devbox-migration-syncoid
    install -d -m 0700 "$state"
    exec 9>"$state/lock"
    flock -x 9
    clients="$state/''${source##*/}"
    install -d -m 0700 "$clients"
    if [ "$action" = enter ]; then
      # Refuse missing children; NEVER fall back to a parent delegation.
      "$zfs" list -H "$source" >/dev/null
      [ "$("$zfs" get -H -o value acltype "$source")" = posix ]
      [ "$("$zfs" get -H -o value xattr "$source")" = sa ]
      if [ -n "$target" ]; then
        "$zfs" list -H "$target" >/dev/null
        [ "$("$zfs" get -H -o value mounted "$target")" = no ]
        [ "$("$zfs" get -H -o value readonly "$target")" = on ]
        [ "$("$zfs" get -H -o value acltype "$target")" = posix ]
        [ "$("$zfs" get -H -o value xattr "$target")" = sa ]
      fi
      # Register before granting; ExecStopPost also cleans failed starts.
      touch "$clients/$job"
      "$zfs" allow -ld -u syncoid send,hold "$source"
      if [ -n "$target" ]; then
        "$zfs" allow -l -u syncoid receive,create,mount "$target"
      fi
    else
      rm -f "$clients/$job"
      if [ -n "$target" ]; then
        "$zfs" unallow -l -u syncoid receive,create,mount "$target"
      fi
      # Both destinations share a source. Only the last client may revoke it.
      shopt -s nullglob
      remaining=("$clients/"*)
      if [ "''${#remaining[@]}" -eq 0 ]; then
        "$zfs" unallow -ld -u syncoid send,hold "$source"
      fi
    fi
  '';
  migrationSyncoidRun = pkgs.writeShellScript "migration-syncoid-verified" ''
    export PATH=${
      lib.makeBinPath [
        pkgs.coreutils
        pkgs.gnugrep
      ]
    }
    set -euo pipefail
    [ "$#" -eq 1 ] || exit 64
    job=$1
    case "$job" in
      migration-evidence-to-tonk) source='tank/var/devbox-migration-evidence-20261002'; target='tonk/backups/var/devbox-migration-evidence-20261002'; host="" ;;
      migration-evidence-to-tunk) source='tank/var/devbox-migration-evidence-20261002'; target='tunk/backups/var/devbox-migration-evidence-20261002'; host="root@hare" ;;
      waydroid-stage-to-tonk) source='tank/var/devbox-waydroid-stage-20261002T082100Z'; target='tonk/backups/var/devbox-waydroid-stage-20261002T082100Z'; host="" ;;
      waydroid-stage-to-tunk) source='tank/var/devbox-waydroid-stage-20261002T082100Z'; target='tunk/backups/var/devbox-waydroid-stage-20261002T082100Z'; host="root@hare" ;;
      waydroid-system-to-tonk) source='tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; target='tonk/backups/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; host="" ;;
      waydroid-system-to-tunk) source='tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; target='tunk/backups/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z'; host="root@hare" ;;
      waydroid-user-to-tonk) source='tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; target='tonk/backups/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; host="" ;;
      waydroid-user-to-tunk) source='tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; target='tunk/backups/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z'; host="root@hare" ;;
      *) echo 'Refusing non-migration job' >&2; exit 64 ;;
    esac
    zfs=/run/booted-system/sw/bin/zfs
    snapshot=$("$zfs" list -H -t snapshot -o name -s creation -r -d 1 "$source" | grep -F "$source@" | tail -1)
    [ -n "$snapshot" ]
    expected=$("$zfs" get -Hp -o value guid "$snapshot")
    if [ -n "$host" ]; then destination="$host:$target"; else destination=$target; fi
    ${config.services.syncoid.package}/bin/syncoid --sendoptions "" --recvoptions u --no-privilege-elevation --no-sync-snap --no-rollback --identifier="$job" "$source" "$destination"
    # Syncoid may exit zero after zfs send emits permission-denied warnings.
    # A job succeeds only if the pinned source snapshot actually reached target.
    if [ -n "$host" ]; then
      actual=$(${pkgs.openssh}/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$host" "zfs get -Hp -o value guid '$target@''${snapshot#*@}'")
    else
      actual=$("$zfs" get -Hp -o value guid "$target@''${snapshot#*@}")
    fi
    [ "$expected" = "$actual" ] || { echo "Snapshot GUID mismatch for $job" >&2; exit 1; }
    printf 'VERIFIED %s source/target snapshot GUID=%s snapshot=%s\n' "$job" "$actual" "$snapshot"
  '';
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
    # The coordinator evidence contains genuine named/default ACL fixtures.
    # Keep them on a separate backed-up child, never widen tank/var's policy.
    "/var/lib/devbox/migration-evidence" = {
      device = "tank/var/devbox-migration-evidence-20261002";
      fsType = "zfs";
      depends = [ "/var" ];
    };
    # These migration-owned children retain their own POSIX ACL/xattr policy.
    # Their mountpoint property is legacy, like /var and /home: order explicit
    # mounts after those parents instead of racing zfs-mount at boot. Never
    # change the parents' ACL policy or depend on their nonrecursive backups.
    "/var/lib/devbox/migration-backups/desktop/waydroid-acl-20261002T082100Z" = {
      device = "tank/var/devbox-waydroid-stage-20261002T082100Z";
      fsType = "zfs";
      depends = [ "/var" ];
    };
    "/var/lib/waydroid" = {
      device = "tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z";
      fsType = "zfs";
      depends = [ "/var" ];
    };
    "/home/pcarrier/.local/share/waydroid" = {
      device = "tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z";
      fsType = "zfs";
      depends = [ "/home" ];
    };
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

  # Fail closed if either migrated tree is absent. The coordinator's existing
  # runtime mask remains authoritative during preparation; this configuration
  # does not remove it or launch Waydroid/Android.
  systemd.services.waydroid-container.unitConfig = {
    RequiresMountsFor = [
      "/var/lib/waydroid"
      "/home/pcarrier/.local/share/waydroid"
    ];
    ConditionPathIsMountPoint = [
      "/var/lib/waydroid"
      "/home/pcarrier/.local/share/waydroid"
    ];
  };

  # Scope compatibility to Waydroid's own mount namespace, never mutate the
  # installed package, global firewall, Docker, or unrelated Android hosts.
  systemd.services.waydroid-container = {
    restartIfChanged = false;
    serviceConfig.BindReadOnlyPaths = [ "${waydroidNftCompat}:${waydroidNetScript}" ];
  };

  # tower.nix deliberately snapshots/replicates the parents nonrecursively.
  # These children therefore need their own perso retention and both existing
  # backup destinations. Do not broaden the parents' policy to recursive.
  systemd.tmpfiles.rules = [
    "d /run/devbox-migration-syncoid 0700 root root - -"
    "L /var/lib/devbox/workspace/migration-hound - pcarrier users - /var/lib/devbox/migration-evidence/coordinator"
  ];

  services.sanoid.datasets = {
    "tank/var/devbox-migration-evidence-20261002".useTemplate = [ "perso" ];
    "tank/var/devbox-waydroid-stage-20261002T082100Z".useTemplate = [ "perso" ];
    "tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z".useTemplate = [ "perso" ];
    "tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z".useTemplate = [ "perso" ];
  };
  # Sanoid owns snapshot creation/retention. Replication never races to create
  # the same sync snapshot, prunes snapshots, rolls back, or mounts receivers.
  # Backup children are provisioned explicitly; absent children fail closed.
  services.syncoid.commands = {
    migration-evidence-to-tonk = {
      source = "tank/var/devbox-migration-evidence-20261002";
      target = "tonk/backups/var/devbox-migration-evidence-20261002";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=migration-evidence-to-tonk"
      ];
    };
    migration-evidence-to-tunk = {
      source = "tank/var/devbox-migration-evidence-20261002";
      target = "root@hare:tunk/backups/var/devbox-migration-evidence-20261002";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=migration-evidence-to-tunk"
      ];
    };
    waydroid-stage-to-tonk = {
      source = "tank/var/devbox-waydroid-stage-20261002T082100Z";
      target = "tonk/backups/var/devbox-waydroid-stage-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-stage-to-tonk"
      ];
    };
    waydroid-stage-to-tunk = {
      source = "tank/var/devbox-waydroid-stage-20261002T082100Z";
      target = "root@hare:tunk/backups/var/devbox-waydroid-stage-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-stage-to-tunk"
      ];
    };
    waydroid-system-to-tonk = {
      source = "tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z";
      target = "tonk/backups/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-system-to-tonk"
      ];
    };
    waydroid-system-to-tunk = {
      source = "tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z";
      target = "root@hare:tunk/backups/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-system-to-tunk"
      ];
    };
    waydroid-user-to-tonk = {
      source = "tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z";
      target = "tonk/backups/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-user-to-tonk"
      ];
    };
    waydroid-user-to-tunk = {
      source = "tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z";
      target = "root@hare:tunk/backups/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z";
      recvOptions = "u";
      extraArgs = [
        "--no-sync-snap"
        "--no-rollback"
        "--identifier=waydroid-user-to-tunk"
      ];
    };
  };
  systemd.services.syncoid-migration-evidence-to-tonk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} migration-evidence-to-tonk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter migration-evidence-to-tonk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave migration-evidence-to-tonk" ];
  };
  systemd.services.syncoid-migration-evidence-to-tunk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} migration-evidence-to-tunk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter migration-evidence-to-tunk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave migration-evidence-to-tunk" ];
  };
  systemd.services.syncoid-waydroid-stage-to-tonk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-stage-to-tonk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-stage-to-tonk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-stage-to-tonk" ];
  };
  systemd.services.syncoid-waydroid-stage-to-tunk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-stage-to-tunk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-stage-to-tunk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-stage-to-tunk" ];
  };
  systemd.services.syncoid-waydroid-system-to-tonk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-system-to-tonk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-system-to-tonk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-system-to-tonk" ];
  };
  systemd.services.syncoid-waydroid-system-to-tunk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-system-to-tunk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-system-to-tunk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-system-to-tunk" ];
  };
  systemd.services.syncoid-waydroid-user-to-tonk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-user-to-tonk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-user-to-tonk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-user-to-tonk" ];
  };
  systemd.services.syncoid-waydroid-user-to-tunk.serviceConfig = {
    ExecStart = lib.mkForce [ "${migrationSyncoidRun} waydroid-user-to-tunk" ];
    # Writable only to root hooks; syncoid cannot enter this 0700 directory.
    BindPaths = [ "/run/devbox-migration-syncoid" ];
    ExecStartPre = lib.mkForce [ "+${migrationSyncoidDelegation} enter waydroid-user-to-tunk" ];
    ExecStopPost = lib.mkForce [ "+${migrationSyncoidDelegation} leave waydroid-user-to-tunk" ];
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
      "/var/lib/devbox/yas-indentbox/package/bin/yas"
    ];
    path = [
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
      ExecStart = "/var/lib/devbox/yas-indentbox/package/bin/yas server --name default --socket /run/yas-indentbox/yas-default.sock";
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
    # YAS needs these executables, not merely their dlopen libraries.
    path = [ pkgs.pipewire pkgs.wireplumber pkgs.dbus pkgs.xwayland-satellite ];
    after = [
      "network-online.target"
      "user@1000.service"
    ];
    unitConfig = {
      RequiresMountsFor = [
        "/src/ultimator/.dev/workspace"
        "/var/lib/devbox/migration-evidence"
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
      YAS_AUDIO = "1";
      YAS_FONT_EXPORT = "1";
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
      UnsetEnvironment = [
        "FLOWER_ADMIN_TOKEN"
        "ULTIMATOR_TOKEN"
        "ULTIMATOR_DIRECTORY_TOKEN"
      ];
      ExecStart = "${pkgs.fish}/bin/fish --login --command 'set -e FLOWER_ADMIN_TOKEN; set -e ULTIMATOR_TOKEN; set -e ULTIMATOR_DIRECTORY_TOKEN; exec ${pkgs.nix}/bin/nix develop /src/ultimator --command ${devboxStart}'";
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
