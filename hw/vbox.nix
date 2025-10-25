{
  boot.initrd.availableKernelModules = [ "virtio_scsi" ];
  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/root";
      fsType = "btrfs";
      options = [
        "noatime"
        "compress=zstd"
        "ssd"
      ];
    };
    "/boot" = {
      device = "/dev/disk/by-label/boot";
      fsType = "vfat";
      options = [
        "fmask=0022"
        "dmask=0022"
      ];
    };
  };
  virtualisation.virtualbox.guest.enable = true;
  networking.useDHCP = true;
}
