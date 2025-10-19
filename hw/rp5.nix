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
            rev = "rp5b";
            sha256 = "sha256-/Jz94waypNkXSURsxpYw/3lZTZf9c9dQxK2tgEi6Qvw=";
          };
          version = "6.17.4-rp5b";
          modDirVersion = "6.17.4";
          extraConfig = ''
            ARCH_QCOM y
            BATTERY_QCOM_FG m
            CHARGER_QCOM_SMB5 m
            DRM_PANEL_DDIC_CH13726A m
            INPUT_PM8941_PWRKEY m
            INPUT_QCOM_SPMI_HAPTICS m
            JOYSTICK_RETROID m
            LEDS_HTR3212 m
            RTC_DRV_PM8XXX m
            SND_SOC_SM8250 m
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
  hardware.bluetooth.enable = true;
  powerManagement.enable = true;
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
  system.replaceDependencies.replacements = [
    {
      original = pkgs.alsa-ucm-conf;
      replacement = pkgs.alsa-ucm-conf.overrideAttrs (old: {
        src = pkgs.fetchFromGitHub {
          owner = "RetroidPocket";
          repo = "alsa-ucm-conf";
          rev = "30989bd";
          sha256 = "sha256-cFYEsavUeD6ZyZ/UqyjZnOcSJuOaSBe6sqEH2wOQddc=";
        };
      });
    }
  ];
}
