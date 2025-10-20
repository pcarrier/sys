{ pkgs, ... }:
{
  boot = {
    initrd.availableKernelModules = [
      "nvme"
    ];
    kernelModules = [ "kvm-intel" ];
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
  networking = {
    networkmanager.enable = true;
  };
  hardware = {
    bluetooth.enable = true;
    cpu.intel.updateMicrocode = true;
    graphics = {
      enable = true;
      extraPackages = with pkgs; [ intel-media-driver ];
    };
  };
  powerManagement.enable = true;
  services = {
    logind.settings.Login = {
      HandleLidSwitch = "ignore";
      HandlePowerKey = "suspend";
    };
    automatic-timezoned.enable = true;
  };
}
