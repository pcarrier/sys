{
  services.dovecot2 = {
    enable = true;
    enableImap = true;
    mailLocation = "maildir:~/Maildir";
  };
}
