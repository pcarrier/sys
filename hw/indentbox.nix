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
      device = "LABEL=root";
      fsType = "ext4";
    };
    "/boot" = {
      device = "LABEL=boot";
      fsType = "vfat";
      options = [
        "fmask=0022"
        "dmask=0022"
      ];
    };
  };
  networking.useDHCP = true;
}
