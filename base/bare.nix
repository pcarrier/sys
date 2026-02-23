{ lib, ... }:
{
  boot.loader = {
    systemd-boot.enable = true;
    efi.canTouchEfiVariables = true;
  };
  programs._1password-gui.enable = true;
  services = {
    tailscale.enable = true;
    fwupd.enable = true;
  };
  networking.firewall.interfaces.tailscale0 = {
    allowedTCPPortRanges = [
      {
        from = 0;
        to = 65535;
      }
    ];
    allowedUDPPortRanges = [
      {
        from = 0;
        to = 65535;
      }
    ];
  };
}
