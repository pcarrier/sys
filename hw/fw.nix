{ pkgs, ... }:
{
  boot = {
    initrd = {
      availableKernelModules = [
        "xhci_pci"
        "thunderbolt"
        "nvme"
        "usbhid"
        "sd_mod"
      ];
      services.udev.rules = ''
        ACTION=="add", SUBSYSTEM=="thunderbolt", ATTR{authorized}=="0", ATTR{authorized}="1"
      '';
    };
    kernelModules = [ "kvm-intel" ];
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
  hardware = {
    cpu.intel.updateMicrocode = true;
    graphics = {
      enable = true;
      extraPackages = with pkgs; [ intel-media-driver ];
    };
  };
  services = {
    logind.lidSwitch = "ignore";
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
      };
    };
    sanoid = {
      enable = true;
      templates = {
        perso = {
          hourly = 48;
          daily = 30;
          weekly = 5;
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
