{
  boot = {
    initrd = {
      availableKernelModules = [ "nvme" "thunderbolt" "ahci" "xhci_pci" "usb_storage" "usbhid" "sd_mod" ];
      services.udev.rules = ''
        ACTION=="add", SUBSYSTEM=="thunderbolt", ATTR{authorized}=="0", ATTR{authorized}="1"
      '';
    };
    kernelModules = [ "kvm-amd" ];
    zfs.extraPools = [
      "tank"
      "tonk"
    ];
  };
  fileSystems = {
    "/" = {
      device = "tank/root";
      fsType = "zfs";
    };
    "/nix" = {
      device = "tank/nix";
      fsType = "zfs";
    };
    "/home" = {
      device = "tank/home";
      fsType = "zfs";
    };
    "/var" = {
      device = "tank/var";
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
    hostId = "12345678";
  };
  security.polkit.enable = true;
  hardware = {
    bluetooth.enable = true;
    cpu.amd.updateMicrocode = true;
    graphics = {
      enable = true;
      enable32Bit = true;
    };
  };
  services = {
    automatic-timezoned.enable = true;
    xserver.videoDrivers = [ "nvidia" ];
    syncoid = {
      enable = true;
      commands = {
        tank-to-tonk = {
          source = "tank";
          target = "tonk/backups/tank";
        };
        home-to-tonk = {
          source = "tank/home";
          target = "tonk/backups/home";
        };
        var-to-tonk = {
          source = "tank/var";
          target = "tonk/backups/var";
        };
        tank-to-tunk = {
          source = "tank";
          target = "root@hare:tunk/backups/tank";
        };
        home-to-tunk = {
          source = "tank/home";
          target = "root@hare:tunk/backups/home";
        };
        var-to-tunk = {
          source = "tank/var";
          target = "root@hare:tunk/backups/var";
        };
      };
    };
    sanoid = {
      enable = true;
      templates = {
        perso = {
          hourly = 48;
          daily = 30;
          weekly = 12;
          monthly = 12;
          yearly = 10;
          autosnap = true;
          autoprune = true;
        };
      };
      datasets = {
        "tank" = {
          useTemplate = [ "perso" ];
        };
        "tank/home" = {
          useTemplate = [ "perso" ];
        };
        "tank/var" = {
          useTemplate = [ "perso" ];
        };
        "tonk" = {
          useTemplate = [ "perso" ];
        };
      };
    };
  };
}
