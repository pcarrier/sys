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
    plymouth = {
      enable = true;
      theme = "steamos";
      themePackages = with pkgs; [ steamdeck-hw-theme ];
    };
  };
  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/root";
      fsType = "btrfs";
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
  networking.networkmanager = {
    enable = true;
    plugins = with pkgs; [
      networkmanager-openvpn
    ];
  };
  hardware.cpu.amd.updateMicrocode = true;
  jovian = {
    steam = {
      enable = true;
      autoStart = true;
      desktopSession = "niri";
      user = "pcarrier";
    };
    devices.steamdeck = {
      enable = true;
      autoUpdate = true;
    };
  };
  programs.niri.enable = true;
}
