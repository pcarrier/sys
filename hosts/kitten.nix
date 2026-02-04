{ lib }:
lib.bare {
  name = "kitten";
  system = "aarch64-linux";
  trusted = true;
  desktop = true;
  hardware = ../hw/macvm.nix;
  extraModules = [
    ../feat/autoniri.nix
  ];
} lib.commonInputs
