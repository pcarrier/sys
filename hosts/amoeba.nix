{ lib }:
lib.bare {
  name = "amoeba";
  trusted = true;
  desktop = true;
  system = "aarch64-linux";
  emulated = [ "x86_64-linux" ];
  hardware = ../hw/odin3ufs.nix;
  extraModules = [
    ../feat/autoniri.nix
  ];
} (lib.commonInputs // { inherit (lib) jovian; })
