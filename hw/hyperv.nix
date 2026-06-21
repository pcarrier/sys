{ lib, ... }:
{
  boot = {
    initrd.availableKernelModules = [
      "hv_vmbus"
      "hv_storvsc"
      "hv_netvsc"
      "hv_utils"
      "sd_mod"
    ];
    initrd.supportedFilesystems = [ "f2fs" ];
    kernelModules = [ "hv_sock" ];
  };
  fileSystems = {
    "/" = lib.mkDefault {
      device = "/dev/disk/by-label/root";
      fsType = "f2fs";
      options = [
        "noatime"
        "compress_algorithm=zstd"
        "atgc"
        "gc_merge"
      ];
    };
    "/boot" = lib.mkDefault {
      device = "/dev/disk/by-label/boot";
      fsType = "vfat";
      options = [
        "fmask=0022"
        "dmask=0022"
      ];
    };
  };
  virtualisation.hypervGuest.enable = true;
}
