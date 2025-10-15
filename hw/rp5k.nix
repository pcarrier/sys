{
  pkgs,
  lib,
  ...
}:
{
  boot = {
    kernelPackages = pkgs.linuxPackagesFor (
      pkgs.linux_6_17.override {
        argsOverride = {
          src = pkgs.fetchFromGitHub {
            owner = "pcarrier";
            repo = "linux";
            rev = "retroid_6.17.3";
            sha256 = "sha256-0Jdv4fQJvMKfh1HdTBmYGfl+j6cFNhIX5rVfUsbaDsQ=";
          };
          version = "6.17.3-retroid";
          modDirVersion = "6.17.3";

          extraConfig = ''
            JOYSTICK_RETROID y
            INPUT_QCOM_SPMI_HAPTICS y
            LEDS_HTR3212 y
            CHARGER_QCOM_SMB5 y
            BATTERY_QCOM_FG y
          '';
        };
      }
    );
    supportedFilesystems = lib.mkForce [
      "btrfs"
      "vfat"
      "cifs"
    ];
  };
  services.udev.extraRules = ''
    SUBSYSTEM=="input", ATTRS{name}=="Retroid Pocket Gamepad", MODE="0666", ENV{ID_INPUT_JOYSTICK}="1"
    SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT}=="1", ATTRS{name}=="pmi8998_haptics", TAG+="uaccess", ENV{FEEDBACKD_TYPE}="vibra"
  '';
}
