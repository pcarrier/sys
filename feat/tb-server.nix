{
  imports = [ ./thunderbolt.nix ];

  # Share the thunderbolt ethernet link with the peer and hand out DHCP
  # addresses from a fixed subnet.
  networking.networkmanager.ensureProfiles.profiles.thunderbolt = {
    connection = {
      id = "Thunderbolt shared";
      type = "ethernet";
      autoconnect = true;
      autoconnect-priority = 100;
      match-device = "driver:thunderbolt-net";
    };
    ipv4 = {
      method = "shared";
      addresses = "10.55.0.1/24";
    };
    ipv6 = {
      method = "disabled";
    };
  };
}
