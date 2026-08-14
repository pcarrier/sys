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
  # PipeWire asks for SCHED_FIFO for its data loop and carries on silently
  # without it, so a machine with no realtime privilege runs its audio graph at
  # ordinary priority against the compositor and video encoders. It then misses
  # its cycle whenever a core saturates — scrolling a window is enough — and the
  # gap is cut into the captured audio before anything is encoded or sent, where
  # no client-side buffer can recover it.
  #
  # RTKit rather than an rtprio limit: it grants the priority per thread to the
  # process that asks and polices what it hands out, and it covers a server run
  # by hand from a dev shell, which a limit on a systemd unit would not.
  security.rtkit.enable = true;
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
