{
  pkgs,
  ...
}:
{
  boot = {
    loader.timeout = 0;
    initrd.availableKernelModules = [
      "sd_mod"
    ];
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
  networking.networkmanager.enable = true;
  security.polkit.enable = true;
  services.greetd = {
    enable = true;
    settings = rec {
      initial_session = {
        command = "sway";
        user = "pcarrier";
      };
      default_session = initial_session;
    };
  };
}
