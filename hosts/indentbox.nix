{ lib }:
lib.ec2 {
  name = "indentbox";
  system = "aarch64-linux";
  extraModules = [
    ../feat/indentmoo.nix
  ];
} lib.commonInputs
