{
  boot = {
    initrd.availableKernelModules = [
      "nvme"
    ];
  };
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
  networking.networkmanager.enable = true;
  security.polkit.enable = true;
  hardware = {
    bluetooth.enable = true;
    cpu.amd.updateMicrocode = true;
    graphics = {
      enable = true;
      enable32Bit = true;
    };
  };
  services.automatic-timezoned.enable = true;
}
