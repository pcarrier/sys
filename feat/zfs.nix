{ pkgs, lib, ... }:
{
  boot = {
    supportedFilesystems = [ "zfs" ];
    kernelPackages = lib.mkForce pkgs.linuxPackages_6_16;
  };
  services.zfs.autoScrub.enable = true;
}
