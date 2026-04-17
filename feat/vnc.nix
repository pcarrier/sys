{ pkgs, ... }:
let
  start = pkgs.writeShellScript "vnc-xfce" ''
    ${pkgs.tigervnc}/bin/Xvnc :1 \
      -geometry 1920x1080 \
      -depth 24 \
      -SecurityTypes None \
      -localhost 0 &
    while ! ${pkgs.xorg.xdpyinfo}/bin/xdpyinfo -display :1 >/dev/null 2>&1; do
      sleep 0.1
    done
    export DISPLAY=:1
    source /etc/set-environment
    exec ${pkgs.dbus}/bin/dbus-run-session --dbus-daemon=${pkgs.dbus}/bin/dbus-daemon ${pkgs.xfce.xfce4-session}/bin/startxfce4
  '';
in
{
  services.xserver.desktopManager.xfce.enable = true;
  environment.systemPackages = [ pkgs.wlvncc ];
  networking.firewall.allowedTCPPorts = [ 5901 ];
  systemd.services.vnc = {
    description = "VNC Server (XFCE)";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "pcarrier";
      ExecStart = start;
      Restart = "on-failure";
      RestartSec = 3;
    };
  };
}
