{ pkgs, ... }:
{
  boot = {
    loader.timeout = 0;
    initrd.availableKernelModules = [
      "sd_mod"
    ];
    kernelPackages = pkgs.linuxPackagesFor (
      pkgs.linux_6_17.override {
        argsOverride = {
          src = pkgs.fetchFromGitHub {
            owner = "pcarrier";
            repo = "linux";
            rev = "rp5";
            sha256 = "sha256-aBRJnBqWoBJN8dUdCylBUKglE/swkGHy8Eoo/7/n/QM=";
          };
          version = "6.17.3-rp5-1";
          modDirVersion = "6.17.3";

          extraConfig = ''
            INPUT_QCOM_SPMI_HAPTICS y
            LEDS_HTR3212 y
            CHARGER_QCOM_SMB5 y
            BATTERY_QCOM_FG y
          '';
        };
      }
    );
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
  swapDevices = [
    {
      device = "/dev/disk/by-label/swap";
    }
  ];
  networking.networkmanager.enable = true;
  programs.niri.enable = true;
  security.polkit.enable = true;
  hardware.bluetooth.enable = true;
  powerManagement.enable = true;
  programs.sway = {
    enable = true;
    wrapperFeatures.gtk = true;
  };
  services = {
    logind.settings.Login = {
      HandlePowerKey = "ignore";
      HandleSuspendKey = "ignore";
      HandleHibernateKey = "ignore";
      HandleLidSwitch = "ignore";
    };
    greetd = {
      enable = true;
      settings = rec {
        initial_session = {
          #command = "niri-session";
          command = "sway";
          user = "pcarrier";
        };
        default_session = initial_session;
      };
    };
    automatic-timezoned.enable = true;
  };
}
