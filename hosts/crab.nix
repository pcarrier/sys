{ lib }:
lib.darwin {
  name = "crab";
  system = "aarch64-darwin";
  extraModules = [
    ../feat/github-runner-darwin.nix
    (
      { pkgs, ... }:
      {
        # Forge CI's jobs on crab (`runs-on: crab`) run through crab's Ultimator computer and YAS
        # server and take their PATH from it, which a login's /run/current-system/sw/bin is on.
        # cmake builds libopus for opusic-sys, which YAS's server pulls in: YAS's macOS packages,
        # built on GitHub's macos-15 today (which has cmake), come here with Forge.
        environment.systemPackages = [ pkgs.cmake ];
      }
    )
  ];
} lib.commonInputs
