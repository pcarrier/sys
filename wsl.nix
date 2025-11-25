{ pkgs, ... }:
{
  wsl = {
    enable = true;
    interop.register = true;
    startMenuLaunchers = true;
    useWindowsDriver = true;
    wrapBinSh = true;
    usbip.enable = true;
    defaultUser = "pcarrier";
    extraBin = with pkgs; [
      { src = "${gnused}/bin/sed"; }
      { src = "${which}/bin/which"; }
      { src = "${mktemp}/bin/mktemp"; }
      { src = "${coreutils}/bin/mv"; }
      { src = "${gnutar}/bin/tar"; }
      { src = "${gzip}/bin/gzip"; }
      { src = "${coreutils}/bin/rm"; }
      { src = "${procps}/bin/ps"; }
      { src = "${gnugrep}/bin/grep"; }
      { src = "${coreutils}/bin/touch"; }
      { src = "${coreutils}/bin/chmod"; }
      { src = "${coreutils}/bin/sleep"; }
      { src = "${coreutils}/bin/tail"; }
      { src = "${wget}/bin/wget"; }
      { src = "${gawk}/bin/awk"; }
    ];
  };
  # for cursor to install its server
}
