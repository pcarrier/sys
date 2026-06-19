{ lib }:
lib.bare {
  name = "monkey";
  system = "aarch64-linux";
  trusted = true;
  hardware = ../hw/hyperv.nix;
} lib.commonInputs
