{ pkgs, lib, ... }:
{
  boot = {
    supportedFilesystems = [ "zfs" ];
    kernelPackages = lib.mkForce pkgs.linuxPackages_6_17;
  };
  services.zfs.autoScrub.enable = true;
}
