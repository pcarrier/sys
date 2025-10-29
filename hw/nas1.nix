{ pkgs, ... }:
{
  boot = {
    initrd = {
      availableKernelModules = [
        "xhci_pci"
        "ahci"
        "sd_mod"
        "sdhci_pci"
      ];
    };
    kernelModules = [ "kvm-intel" ];
  };
  environment.systemPackages = with pkgs; [
    mbuffer
    lzop
  ];
  fileSystems = {
    "/" = {
      device = "tunk/root";
      fsType = "zfs";
    };
    "/nix" = {
      device = "tunk/nix";
      fsType = "zfs";
    };
    "/home" = {
      device = "tunk/home";
      fsType = "zfs";
    };
    "/var" = {
      device = "tunk/var";
      fsType = "zfs";
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
  networking = {
    networkmanager.enable = true;
    hostId = "87654321";
  };
  hardware.cpu.intel.updateMicrocode = true;
}
