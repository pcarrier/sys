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
      { src = "${coreutils}/bin/chmod"; }
      { src = "${coreutils}/bin/mv"; }
      { src = "${coreutils}/bin/rm"; }
      { src = "${coreutils}/bin/sleep"; }
      { src = "${coreutils}/bin/tail"; }
      { src = "${coreutils}/bin/touch"; }
      { src = "${gawk}/bin/awk"; }
      { src = "${gnugrep}/bin/grep"; }
      { src = "${gnused}/bin/sed"; }
      { src = "${gnutar}/bin/tar"; }
      { src = "${gzip}/bin/gunzip"; }
      { src = "${gzip}/bin/gzip"; }
      { src = "${mktemp}/bin/mktemp"; }
      { src = "${procps}/bin/ps"; }
      { src = "${wget}/bin/wget"; }
      { src = "${which}/bin/which"; }
    ];
  };
  # for cursor to install its server
}
