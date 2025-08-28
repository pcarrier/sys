{
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;
  services.tailscale.enable = true;
  networking.firewall.interfaces."tailscale0" = {
    allowedTCPPorts = [ 3389 ];
    allowedUDPPorts = [ 3389 ];
  };
}
