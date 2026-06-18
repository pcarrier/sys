{ ... }:
{
  boot = {
    initrd.availableKernelModules = [ "thunderbolt" ];
    kernelModules = [ "thunderbolt-net" ];
  };

  # Authorize thunderbolt devices automatically so the networking device
  # appears as soon as the cable is plugged in.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="thunderbolt", ATTR{authorized}=="0", ATTR{authorized}="1"
  '';
}
