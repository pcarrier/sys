# hound as the i0-i2 cluster's sandbox Docker host: a peer of the cluster's
# WireGuard mesh (10.77.0.20, behind NAT, so it dials i0-i2 and keeps the tunnels
# open), reached over SSH as pcarrier with a key restricted to
# `docker system dial-stdio` from the mesh addresses. The cluster side is
# xmit-dev/ultimator deploy/nixos (inventory.sandboxHost).
# The private key stays on hound: /var/lib/ultimator-mesh/wg.key (root, 0600).
{ config, lib, ... }:
lib.mkIf (config.networking.hostName == "hound") {
  networking.wireguard.interfaces.wg-ultimator = {
    ips = [ "10.77.0.20/32" ];
    privateKeyFile = "/var/lib/ultimator-mesh/wg.key";
    mtu = 1380;
    # Replace an interface made by hand before this unit existed.
    preSetup = "ip link del dev wg-ultimator 2>/dev/null || true";
    peers = [
      {
        name = "i0";
        publicKey = "ffQ1rJq0JrP+yf1419ZADFTpxHxD+oJ326ZtomxA+Fo=";
        endpoint = "5.9.17.236:51820";
        allowedIPs = [ "10.77.0.1/32" ];
        persistentKeepalive = 25;
      }
      {
        name = "i1";
        publicKey = "FbPVc2EWCutTs80EFbLovM1KVukWP22IvLj/VMiT7kQ=";
        endpoint = "5.9.17.142:51820";
        allowedIPs = [ "10.77.0.2/32" ];
        persistentKeepalive = 25;
      }
      {
        name = "i2";
        publicKey = "lKqYjnEjP4IQTUMnLhJPcT3dmAgp6Gx5MMZfpJDfSTM=";
        endpoint = "5.9.17.82:51820";
        allowedIPs = [ "10.77.0.3/32" ];
        persistentKeepalive = 25;
      }
    ];
  };
  users.users.pcarrier.openssh.authorizedKeys.keys = [
    ''restrict,from="10.77.0.1,10.77.0.2,10.77.0.3",command="docker system dial-stdio" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOa66Nk1fl5Iq/GQoIsd6l38Q+CHQRdhcE7YH9zLBQgT ultimator-cluster-sandboxes''
  ];
}
