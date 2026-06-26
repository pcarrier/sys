{
  users.users.pcarrier.extraGroups = [ "uinput" ];
  services.sunshine = {
    enable = true;
    autoStart = true;
    capSysAdmin = true; # required for Wayland (niri) KMS capture
    openFirewall = true;
  };
}
