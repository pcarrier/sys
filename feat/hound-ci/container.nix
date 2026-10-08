# The NixOS system each hound-ci job boots in a fresh systemd-nspawn container.
# Host side (feat/hound-ci.nix, supervisor.py `job`): an empty per-job root on its
# own ZFS dataset, the host /nix/store bound read-only, a private user namespace
# (no host UID in the container), a private network namespace reached only through
# a per-slot veth NATed by the host (the hound_ci nft table's job chains drop its
# input to the host, spoofed sources, IPv6 and private destinations), a read-only
# sysfs from a fresh network namespace for Docker, the slot's cgroup caps, and a single-use JIT runner config as
# the systemd credential `jit`. The container powers off when its one job ends.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
  # What scripts/ci-native-deps.sh asks of Ubuntu, and what the app's tests need:
  # the C toolchain, CMake (Opus, aws-lc), pkg-config, libclang (bindgen), OpenSSL,
  # Vulkan (lavapipe), mkcert, D-Bus, fonts, Chrome, WebKitGTK through Xvfb.
  devInputs = with pkgs; [
    openssl
    zlib
    vulkan-loader
    dbus
    glib
    gtk3
    webkitgtk_4_1
  ];
  # The job's build environment, as stdenv's setup hooks compute it for these
  # inputs (NIX_CFLAGS_COMPILE, NIX_LDFLAGS, PKG_CONFIG_PATH…), the way
  # `nix develop` would, but fixed at build time: jobs get no Nix daemon.
  devEnv =
    pkgs.runCommandCC "hound-ci-dev-env"
      {
        nativeBuildInputs = [ pkgs.pkg-config ];
        buildInputs = devInputs;
      }
      ''
        for name in NIX_CFLAGS_COMPILE NIX_LDFLAGS PKG_CONFIG_PATH \
          NIX_CC_WRAPPER_TARGET_HOST_x86_64_unknown_linux_gnu \
          NIX_BINTOOLS_WRAPPER_TARGET_HOST_x86_64_unknown_linux_gnu NIX_HARDENING_ENABLE \
          NIX_ENFORCE_NO_NATIVE NIX_PKG_CONFIG_WRAPPER_TARGET_HOST_x86_64_unknown_linux_gnu; do
          if [ -n "''${!name+x}" ]; then
            printf 'export %s=%q\n' "$name" "''${!name}"
          fi
        done > $out
      '';
  libraryPath = lib.makeLibraryPath (
    devInputs
    ++ [
      pkgs.stdenv.cc.cc.lib
      pkgs.libGL
    ]
  );
  # The runner's whole environment (it starts from env -i), as shell exports.
  runnerVars = {
    HOME = "/home/runner";
    USER = "runner";
    LOGNAME = "runner";
    LANG = "C.UTF-8";
    PATH = "/home/runner/.cargo/bin:/run/wrappers/bin:/run/current-system/sw/bin";
    CARGO_HOME = "/home/runner/.cargo";
    RUSTUP_HOME = "/home/runner/.rustup";
    RUNNER_TOOL_CACHE = "/opt/hostedtoolcache";
    LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
    BINDGEN_EXTRA_CLANG_ARGS = "-isystem ${lib.getDev pkgs.stdenv.cc.libc}/include";
    LD_LIBRARY_PATH = libraryPath;
    GI_TYPELIB_PATH = lib.makeSearchPath "lib/girepository-1.0" [
      pkgs.gtk3
      pkgs.webkitgtk_4_1
      pkgs.glib.out
      pkgs.gobject-introspection
      pkgs.pango.out
      pkgs.gdk-pixbuf
      pkgs.harfbuzz
      pkgs.at-spi2-core
      pkgs.libsoup_3
    ];
    CHROME_BIN = "/usr/bin/google-chrome";
    # nixpkgs' github-runner ships externals/node24 only (Node 20 is gone from nixpkgs).
    # Node 20 JavaScript actions already run on it, but hashFiles() and the runner's other
    # internal scripts ask for node20 unless told otherwise (10-08: setup-ci's
    # `hashFiles('ultimator/flake.lock')` failed with "…/externals/node20/bin/node: No such file").
    ACTIONS_RUNNER_FORCED_INTERNAL_NODE_VERSION = "node24";
    ACTIONS_RUNNER_FORCE_ACTIONS_NODE_VERSION = "node24";
    HOUND_CI_CONTAINER = "nspawn-nixos";
  };
  runnerEnv = pkgs.writeText "hound-ci-runner.env" (
    lib.concatStrings (
      lib.mapAttrsToList (name: value: "export ${name}=${lib.escapeShellArg value}\n") runnerVars
    )
  );
  start = pkgs.writeShellApplication {
    name = "hound-ci-start";
    runtimeInputs = with pkgs; [
      coreutils
      gnugrep
      iproute2
      python3
      docker
      util-linux
      systemd
    ];
    # Its bash -c programs expand their own positional parameters.
    excludeShellChecks = [ "SC2016" ];
    text = builtins.readFile ./container-start.sh;
  };
in
{
  boot.isContainer = true;
  system.stateVersion = "26.05";
  networking = {
    hostName = "hound-ci";
    useDHCP = false;
    useHostResolvConf = false;
    firewall.enable = false;
    resolvconf.enable = false;
  };
  # The host configures host0 (supervisor job-network); these resolvers are public, never the host's.
  environment.etc."resolv.conf".text =
    "nameserver 1.1.1.1\nnameserver 9.9.9.9\noptions timeout:5 attempts:1\n";
  # No Nix in jobs: the host store is bound read-only and no daemon socket exists.
  nix.enable = false;
  documentation.enable = false;
  services.journald.settings.Journal = {
    Storage = "volatile";
    RuntimeMaxUse = "64M";
  };
  services.udisks2.enable = false;
  security.polkit.enable = false;
  time.timeZone = "UTC";

  users.mutableUsers = false;
  # Nobody logs in: the host starts the job service, and the container ends with it.
  users.allowNoPasswordLogin = true;
  users.users.runner = {
    isNormalUser = true;
    uid = 1000;
    home = "/home/runner";
    extraGroups = [
      "docker"
      "wheel"
    ];
  };
  # Root inside the container is an unprivileged host UID (private user namespace).
  security.sudo = {
    enable = true;
    wheelNeedsPassword = false;
  };

  virtualisation.docker = {
    enable = true;
    daemon.settings = {
      storage-driver = "overlay2";
      # runc refuses to start containers here: nspawn gives this user namespace no
      # mountable sysfs (its /sys is a tmpfs of read-only sysfs binds), and runc
      # mounts a fresh one. crun falls back to binding /sys instead.
      default-runtime = "crun";
      runtimes.crun.path = "${pkgs.crun}/bin/crun";
      # The job's own daemon; its bridge stays inside the container's namespace.
      bip = "172.31.255.1/24";
      default-address-pools = [
        {
          base = "172.30.0.0/16";
          size = 24;
        }
      ];
      dns = [
        "1.1.1.1"
        "9.9.9.9"
      ];
    };
  };

  # Dynamically linked downloads (setup-node's Node, rustup's toolchains, actions'
  # helpers) run through nix-ld, as on a glibc distribution.
  programs.nix-ld = {
    enable = true;
    libraries = with pkgs; [
      stdenv.cc.cc.lib
      zlib
      zstd
      openssl
      curl
      icu
      libxml2
      xz
      bzip2
      util-linux
      systemd
      glib
      nss
      nspr
      expat
    ];
  };
  hardware.graphics.enable = true;
  fonts.packages = [ pkgs.dejavu_fonts ];

  environment.systemPackages = with pkgs; [
    bashInteractive
    coreutils
    findutils
    diffutils
    gnugrep
    gnused
    gawk
    gnutar
    gzip
    bzip2
    xz
    zstd
    unzip
    zip
    which
    file
    procps
    psmisc
    iproute2
    git
    git-lfs
    curl
    wget
    jq
    openssh
    openssl
    python
    stdenv.cc
    gnumake
    cmake
    pkg-config
    llvmPackages.clang-unwrapped
    llvmPackages.libclang
    perl
    patchelf
    rustup
    nodejs_26
    google-chrome
    kubernetes-helm
    gh
    powershell
    mkcert
    dbus
    xvfb-run
    xauth
    docker-buildx
    vulkan-tools
  ];
  environment.etc."hound-ci/runner.env".source = runnerEnv;
  environment.etc."hound-ci/dev.env".source = devEnv;
  # Paths the Ubuntu VMs had and the workflows name.
  systemd.tmpfiles.rules = [
    "L+ /usr/bin/google-chrome - - - - ${pkgs.google-chrome}/bin/google-chrome-stable"
    "L+ /usr/bin/python3 - - - - ${python}/bin/python3"
    "L+ /bin/bash - - - - ${pkgs.bashInteractive}/bin/bash"
    "L+ /usr/bin/bash - - - - ${pkgs.bashInteractive}/bin/bash"
    "d /opt/hostedtoolcache 0755 runner users -"
  ];

  systemd.services.hound-ci-job = {
    description = "One GitHub Actions job (single-use JIT runner), then power off";
    wantedBy = [ "multi-user.target" ];
    after = [ "docker.service" ];
    requires = [ "docker.service" ];
    # One job, then the container ends; the host reads the console markers.
    unitConfig = {
      SuccessAction = "poweroff-force";
      FailureAction = "poweroff-force";
    };
    serviceConfig = {
      Type = "exec";
      ImportCredential = "jit";
      ExecStart = "${start}/bin/hound-ci-start ${pkgs.github-runner} ${runnerEnv} ${devEnv}";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      TimeoutStartSec = "infinity";
    };
  };
}
