{
  boot = {
    initrd.availableKernelModules = [
      "nvme"
      "ahci"
      "xhci_pci"
      "usb_storage"
      "usbhid"
      "sd_mod"
    ];
    swraid = {
      enable = true;
      mdadmConf = "MAILADDR root";
    };
  };
  fileSystems = {
    "/" = {
      device = "/dev/md0";
      fsType = "ext4";
    };
    "/boot" = {
      device = "/dev/nvme0n1p1";
      fsType = "vfat";
      options = [
        "fmask=0022"
        "dmask=0022"
      ];
    };
  };
  networking.useDHCP = true;
}
