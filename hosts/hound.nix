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
    ../folks/dauriac.nix
  ];
} (lib.commonInputs // { inherit (lib) jovian; })
