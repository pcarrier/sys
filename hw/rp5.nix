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
            sha256 = "sha256-aawxHSfbkveKu01is9IKn02VdCpx7XqZ0lX1hotgRhE=";
          };
          version = "6.17.3-rp5-1";
          modDirVersion = "6.17.3";
          extraConfig = ''
            BATTERY_QCOM_FG m
            CHARGER_QCOM_SMB5 m
            DRM_PANEL_DDIC_CH13726A m
            INPUT_PM8941_PWRKEY m
            INPUT_QCOM_SPMI_HAPTICS m
            LEDS_HTR3212 m
            RTC_DRV_PM8XXX m
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
  security.polkit.enable = true;
  hardware = {
    bluetooth.enable = true;
    fancontrol = {
      enable = true;
      config = '''';
    };
  };
  powerManagement.enable = true;
  programs.niri.enable = true;
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
