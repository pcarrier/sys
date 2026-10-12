{ lib }:
lib.bare {
  name = "hound";
  trusted = true;
  desktop = true;
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  hardware = ../hw/tower.nix;
  extraModules = [
    ../feat/sandbox-ssh.nix
    ../feat/yas.nix
    ../feat/flatpak.nix
    ../feat/github-runner.nix
    ../feat/ultimator-cluster-cd.nix
    ../feat/ultimator-release-build.nix
    ../feat/forge-deploy.nix
    ../feat/hound-ci.nix
    {
      services.hound-ci = {
        enable = true;
        imageName = "base-cache-v2.qcow2";
      };
    }
    ../feat/hound-devbox.nix
    ../feat/ultimator-mesh.nix
    ../feat/waydroid.nix
    ../feat/libk.nix
    ../feat/mail.nix
    ../feat/media.nix
    ../feat/mymoo.nix
    ../feat/nvidia.nix
    ../feat/ollama.nix
    ../feat/plentys.nix
    ../feat/plugdev.nix
    ../feat/print.nix
    ../feat/steam.nix
    ../feat/sunshine.nix
    ../feat/zfs.nix
    {
      # No weekly full `zpool trim`: on tank's four 870 QVO (QLC) SSDs it ran for days (10-12: 16-26 % after 12 h,
      # IO pressure ~82 %), slowing every release build, Docker container start and CI job on this host all along
      # (deploy #234: 337 s before its keys; incident Ops:incidents/hound-trim-slows-release-build-20261012.md).
      # Freed blocks are trimmed as they go instead, with the pool's autotrim, set once by hand (a pool property):
      #   doas zpool set autotrim=on tank
      services.zfs.trim.enable = false;
    }
    ../folks/dauriac.nix
  ];
} (lib.commonInputs // { inherit (lib) jovian; })
