{ lib, pkgs, ... }:
{
  boot = {
    initrd.availableKernelModules = [
      "sd_mod"
      "ufshcd_pltfrm"
      "ufs_qcom"
    ];
    kernelPackages = pkgs.linuxPackagesFor (
      (pkgs.linux_6_18.override {
        argsOverride = {
          src = pkgs.fetchFromGitHub {
            owner = "pcarrier";
            repo = "linux";
            rev = "v6.19-rc6-odin3";
            sha256 = "sha256-OVoc/L7G+LJXMCv41rjW6gETyFmav6doeN+N7D+Zi8o=";
          };
          version = "6.19-rc6-odin3";
          modDirVersion = "6.19.0-rc6";
        };
      }).overrideAttrs (old: {
        structuredExtraConfig = (old.structuredExtraConfig or { }) // {
          DRM_PANEL_CHIPONE_ICNA3520 = lib.kernel.yes;
        };
      })
    );
  };

  nixpkgs.overlays = [
    (final: prev: {
      mesa = prev.mesa.overrideAttrs (old: {
        src = final.fetchFromGitHub {
          owner = "pcarrier";
          repo = "mesa-tu8";
          rev = "main";
          sha256 = "";
        };
      });
    })
  ];
}
