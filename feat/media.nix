{
  services = {
    nzbget = {
      enable = true;
      user = "pcarrier";
    };
    plex = {
      enable = true;
      openFirewall = true;
    };
    samba = {
      enable = true;
      settings = {
        tank = {
          path = "/tank";
        };
        tonk = {
          path = "/tonk";
        };
      };
    };
  };
}
