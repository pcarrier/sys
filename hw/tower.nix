{
  boot = {
    initrd.availableKernelModules = [
      "nvme"
      "xhci_pci"
      "ahci"
      "usbhid"
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
    graphics.enable = true;
    nvidia.open = true;
  };
  services = {
    xserver.videoDrivers = [ "nvidia" ];
    automatic-timezoned.enable = true;
  };
}
