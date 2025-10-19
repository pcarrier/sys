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
            sha256 = "sha256-di5tKfuvIAksw+aBySecekqyiaxowEbOiF1O7R4bWQA=";
          };
          version = "6.17.3-rp5-1";
          modDirVersion = "6.17.3";
          extraConfig = ''
            ARCH_QCOM y
            BATTERY_QCOM_FG y
            CHARGER_QCOM_SMB5 y
            DRM_PANEL_DDIC_CH13726A m
            INPUT_PM8941_PWRKEY y
            INPUT_QCOM_SPMI_HAPTICS y
            JOYSTICK_RETROID y
            LEDS_HTR3212 y
            RTC_DRV_PM8XXX y
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
#    fancontrol = {
#      enable = true;
#      config = '''';
#    };
  };
  powerManagement.enable = true;
  programs.niri.enable = true;
  services = {
    logind.settings.Login.HandleSuspendKey = "ignore";
    logind.settings.Login.HandleHibernateKey = "ignore";
    logind.settings.Login.HandlePowerKey = "ignore";
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
