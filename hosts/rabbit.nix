{ lib }:
lib.bare {
  name = "rabbit";
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  hardware = ../hw/zfw.nix;
  extraModules = [
    ../feat/blit.nix
    ../feat/flatpak.nix
    ../feat/nvidia.nix
    ../feat/libk.nix
    ../feat/mail.nix
    ../feat/media.nix
    ../feat/plentys.nix
    ../feat/zfs.nix
  ];
} lib.commonInputs
