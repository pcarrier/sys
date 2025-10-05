{
  config,
  pkgs,
  ...
}:
{
  wsl = {
    enable = true;
    interop.register = true;
    startMenuLaunchers = true;
    useWindowsDriver = true;
    wrapBinSh = true;
    defaultUser = "pcarrier";
  };
}
