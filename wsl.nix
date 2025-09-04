{
  config,
  ...
}:
{
  wsl = {
    enable = true;
    interop.register = true;
    startMenuLaunchers = true;
    useWindowsDriver = true;
    wrapBinSh = true;
    extraBin = [
      {
        name = "bash";
        src = config.wsl.binShExe;
      }
    ];
    defaultUser = "pcarrier";
  };
}
