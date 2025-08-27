_: {
  boot.initrd.availableKernelModules = [
    "nvme"
    "xhci_pci"
    "ahci"
    "usbhid"
    "uas"
    "sd_mod"
  ];
  boot.kernelModules = [ "kvm-amd" ];
  fileSystems."/" = {
    device = "/dev/disk/by-label/root";
    fsType = "btrfs";
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-label/boot";
    fsType = "vfat";
    options = [
      "fmask=0022"
      "dmask=0022"
    ];
  };

  networking.useDHCP = true;
  hardware.cpu.amd.updateMicrocode = true;
  boot.swraid.enable = true;
}
