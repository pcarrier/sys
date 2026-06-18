{
  imports = [ ./thunderbolt.nix ];

  # Pick up an address over the thunderbolt ethernet link from the peer's
  # DHCP server.
  networking.networkmanager.ensureProfiles.profiles.thunderbolt = {
    connection = {
      id = "Thunderbolt DHCP";
      type = "ethernet";
      autoconnect = true;
      autoconnect-priority = 100;
      match-device = "driver:thunderbolt-net";
    };
    ipv4 = {
      method = "auto";
    };
    ipv6 = {
      method = "disabled";
    };
  };

  networking.hosts."10.55.0.1" = [ "rabbit" ];
}
