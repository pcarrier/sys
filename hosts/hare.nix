{ lib }:
lib.bare {
  name = "hare";
  system = "x86_64-linux";
  hardware = ../hw/nas1.nix;
  extraModules = [
    ../feat/mail.nix
    ../feat/zfs.nix
  ];
} lib.commonInputs
