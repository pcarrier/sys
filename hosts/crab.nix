{ lib }:
lib.darwin {
  name = "crab";
  system = "aarch64-darwin";
  extraModules = [ ../feat/github-runner-darwin.nix ];
} lib.commonInputs
