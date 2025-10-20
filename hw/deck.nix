{
  pkgs,
  ...
}:
{
  boot = {
    loader.timeout = 0;
    kernelModules = [ "kvm-amd" ];
    initrd.availableKernelModules = [
      "nvme"
      "xhci_pci"
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
  hardware.cpu.amd.updateMicrocode = true;
  jovian = {
    steam = {
      enable = false;
      autoStart = false;
      desktopSession = "niri";
      user = "pcarrier";
    };
    devices.steamdeck = {
      enable = true;
      autoUpdate = true;
    };
  };
  programs.niri.enable = true;
  services = {
    logind.settings.Login.HandlePowerKey = "suspend";
    greetd = {
      enable = true;
      settings = rec {
        initial_session = {
          command = "niri-session";
          user = "pcarrier";
        };
        default_session = initial_session;
      };
    };
    automatic-timezoned.enable = true;
  };
}
