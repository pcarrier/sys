{ pkgs, ... }:
{
  environment.systemPackages = [ pkgs.cifs-utils ];

  fileSystems = {
    "/tank" = {
      device = "//rabbit/tank";
      fsType = "cifs";
      options = [
        "credentials=/etc/cifscreds"
        "noperm"
        "vers=3.1.1"
        "x-systemd.automount"
        "noauto"
        "_netdev"
      ];
    };
    "/tonk" = {
      device = "//rabbit/tonk";
      fsType = "cifs";
      options = [
        "credentials=/etc/cifscreds"
        "noperm"
        "vers=3.1.1"
        "x-systemd.automount"
        "noauto"
        "_netdev"
      ];
    };
  };
}
