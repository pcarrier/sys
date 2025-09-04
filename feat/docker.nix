{
  virtualisation.docker = {
    enable = true;
    extraOptions = "--insecure-registry 10.42.42.42:5000";
  };
  users.users.pcarrier.extraGroups = [ "docker" ];
}
