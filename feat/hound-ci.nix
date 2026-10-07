# Disposable, repository-bound CI jobs: one fresh systemd-nspawn NixOS container per
# job (feat/hound-ci/container.nix). Not the trusted release runner.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.hound-ci;
  slots = lib.range 1 cfg.workers;
  # The last reservedMainSlots slots serve only main's push/manual runs
  # (runs-on hound-ci-main); the others serve both. With 0 no --labels is
  # passed and every slot registers the original four labels.
  slotLabels =
    n:
    if n > cfg.workers - cfg.reservedMainSlots then
      [
        "self-hosted"
        "Linux"
        "X64"
        "hound-ci-main"
      ]
    else
      [
        "self-hosted"
        "Linux"
        "X64"
        "hound-ci"
        "hound-ci-main"
      ];
  labelArgs =
    n:
    lib.optionalString (
      cfg.reservedMainSlots > 0
    ) " --labels ${lib.concatStringsSep " " (slotLabels n)}";
  users = [ "hound-ci-image" ] ++ map (n: "hound-ci-${toString n}") slots;
  # The NixOS system every job boots; built from this flake's nixpkgs.
  container = (pkgs.nixos ./hound-ci/container.nix).config.system.build.toplevel;
  # Its closure: the only store paths a job sees (supervisor.py store_view).
  closure = pkgs.closureInfo { rootPaths = [ container ]; };
  supervisor = pkgs.writeShellApplication {
    name = "hound-ci";
    runtimeInputs = with pkgs; [
      python3
      qemu_kvm
      xorriso
      curl
      gh
      util-linux
      iproute2
      nftables
      config.boot.zfs.package
      config.systemd.package
    ];
    text = ''exec python3 ${./hound-ci/supervisor.py} "$@"'';
  };
  common = {
    User = "root";
    Group = "root";
    UMask = "0077";
    ProtectHome = true;
    # The controller shares the unit's private-network denies. Host DNS may be
    # loopback/LAN; bind public resolvers only inside this unit, not on hound.
    BindReadOnlyPaths = [
      "${pkgs.writeText "hound-ci-resolv.conf" "nameserver 1.1.1.1\nnameserver 9.9.9.9\noptions timeout:5 attempts:1\n"}:/etc/resolv.conf"
    ];
    ProtectSystem = "strict";
    PrivateTmp = true;
    NoNewPrivileges = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
    LockPersonality = true;
    RestrictRealtime = true;
    RestrictAddressFamilies = [
      "AF_UNIX"
      "AF_INET"
      "AF_INET6"
      "AF_NETLINK"
    ];
    CapabilityBoundingSet = [
      "CAP_CHOWN"
      "CAP_DAC_OVERRIDE"
      "CAP_SETUID"
      "CAP_SETGID"
      "CAP_SETPCAP"
    ];
    DevicePolicy = "closed";
    ReadWritePaths = [ "/var/lib/hound-ci" ];
    InaccessiblePaths = [
      "/src"
      "/var/lib/devbox"
      "-/var/lib/secrets"
      "-/run/docker.sock"
      "-/run/containerd"
      "-/run/user"
      "-/tmp/.X11-unix"
    ];
    KillMode = "control-group";
    TimeoutStopSec = "90s";
    CPUWeight = 20;
    IOWeight = 20;
    TasksMax = 512;
    # IPv6 is disabled in SLIRP as well; host filter remains defense in depth.
    IPAddressDeny = [
      "::/0"
      "localhost"
      "link-local"
      "multicast"
      "0.0.0.0/8"
      "10.0.0.0/8"
      "100.64.0.0/10"
      "172.16.0.0/12"
      "192.168.0.0/16"
      "198.18.0.0/15"
      "240.0.0.0/4"
    ];
  };
in
{
  options.services.hound-ci = {
    enable = lib.mkEnableOption "a pool of disposable GitHub CI containers, one per job";
    workers = lib.mkOption {
      type = lib.types.ints.between 1 4;
      default = 4;
    };
    repository = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+";
      default = "xmit-dev/ultimator";
    };
    storageDataset = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9_-]+/hound-ci";
      default = "tank/hound-ci";
      description = "Dedicated CI-only ZFS dataset, hard quota/refquota 512 GiB; existing mismatched properties fail without mutation";
    };
    reservedMainSlots = lib.mkOption {
      type = lib.types.ints.between 0 3;
      default = 0;
      description = "Last N slots register only hound-ci-main (main's push/manual runs); 0 keeps the original labels";
    };
    ghCredentialFile = lib.mkOption {
      type = lib.types.path;
      default = "/home/pcarrier/.config/gh/hosts.yml";
      description = "Root controller credential source; never shared with QEMU or guest";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.reservedMainSlots < cfg.workers;
        message = "services.hound-ci.reservedMainSlots must leave at least one hound-ci slot";
      }
    ];
    users.users = lib.genAttrs users (name: {
      isSystemUser = true;
      group = name;
      home = "/var/empty";
      extraGroups = [ "kvm" ];
    });
    users.groups = lib.genAttrs users (_: { });
    systemd.tmpfiles.rules = [ "d /var/lib/hound-ci 0751 root root -" ];
    systemd.slices.hound-ci = {
      description = "Low-priority isolated CI pool (72 GiB aggregate ceiling)";
      sliceConfig = {
        CPUWeight = 20;
        IOWeight = 20;
        CPUQuota = "2400%";
        MemoryMax = "72G";
      };
    };
    systemd.services = {
      hound-ci-storage = {
        description = "Create/mount only the dedicated CI ZFS dataset with 512 GiB hard quota";
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = "${supervisor}/bin/hound-ci storage --dataset ${cfg.storageDataset}";
        };
      };
      hound-ci-firewall = {
        description = "Deny CI job containers (and the legacy QEMU UIDs) host, private and LAN networks";
        wantedBy = [ "multi-user.target" ];
        after = [
          "network-online.target"
          "nftables.service"
        ];
        wants = [ "network-online.target" ];
        before = map (n: "hound-ci-${toString n}.service") slots;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = "${supervisor}/bin/hound-ci firewall --count ${toString cfg.workers}";
        };
      };
    }
    // lib.listToAttrs (
      map (
        n:
        lib.nameValuePair "hound-ci-${toString n}" {
          description = "Disposable GitHub CI container slot ${toString n}";
          wantedBy = [ "multi-user.target" ];
          # Wants, not Requires: a firewall restart must not restart the slots
          # and kill their jobs. job-prepare refuses a container without the
          # slot's nft chains, and the job unit's IPAddressDeny stays meanwhile.
          requires = [ "hound-ci-storage.service" ];
          wants = [ "hound-ci-firewall.service" ];
          after = [
            "hound-ci-storage.service"
            "hound-ci-firewall.service"
            "network-online.target"
          ];
          unitConfig = {
            StartLimitIntervalSec = "1h";
            StartLimitBurst = 4;
          };
          serviceConfig = common // {
            Slice = "hound-ci.slice";
            # The controller only talks to GitHub and asks PID 1 for the job unit
            # (hound-ci-job-N.service), which holds the job's caps and runs nspawn.
            ExecStart = "${supervisor}/bin/hound-ci worker --slot ${toString n} --repo ${cfg.repository} --system ${container} --store-paths ${closure}/store-paths --helper ${supervisor}/bin/hound-ci --nspawn ${config.systemd.package}/bin/systemd-nspawn --dataset ${cfg.storageDataset}${labelArgs n}";
            LoadCredential = [ "gh-hosts:${cfg.ghCredentialFile}" ];
            RuntimeDirectory = "hound-ci-${toString n}";
            RuntimeDirectoryMode = "0700";
            Restart = "always";
            RestartSec = "10s";
            CPUQuota = "100%";
            MemoryMax = "1G";
          };
        }
      ) slots
    );
  };
}
