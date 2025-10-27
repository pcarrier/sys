{
  pkgs,
  nixpkgs-master,
  lib,
  nix-index,
  system,
  systemType,
  baze,
  tomorrowTheme,
  trusted ? false,
  desktop ? false,
  ...
}:
let
  pkgs-master = import nixpkgs-master {
    inherit system;
    config.allowUnfree = true;
  };

  clip = pkgs.stdenv.mkDerivation {
    name = "clip";
    src = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/sentriz/cliphist/refs/heads/master/contrib/cliphist-fuzzel-img";
      sha256 = "sha256-NgQ87yZCusF/FYprJJ+fvkA3VdrvHp4LyylQ0ajBvjU=";
    };
    phases = [ "installPhase" ];
    installPhase = ''
      install -Dm755 $src $out/bin/clip
    '';
  };

  postcheckout = pkgs.writeShellScriptBin "postcheckout" ''
    SELF="''${BASH_SOURCE[0]}" exec ${pkgs.deno}/bin/deno run -A ${./postcheckout.ts} "$@"
  '';

  baseConfig = {
    home = {
      stateVersion = "25.11";
      username = "pcarrier";
      homeDirectory = "/home/pcarrier";
      packages = with pkgs; [
        bat
        baze.packages.${system}.default
        bubblewrap
        codex
        dconf # for https://github.com/nix-community/home-manager/issues/3113
        dive
        fd
        fastfetch
        ffmpeg
        file
        fio
        gnuplot
        jo
        jq
        ldns
        libarchive
        lnav
        lsof
        moreutils
        mosh
        mpv
        ncdu
        nil
        nixd
        nix-index.packages.${system}.default
        nixos-shell
        nixfmt
        nmap
        nodejs
        perf
        pssh
        rclone
        ripgrep
        ookla-speedtest
        sshfs
        sysstat
        tk
        tokei
        tree
        yt-dlp
        zoxide
        postcheckout
      ];
      sessionVariables.ZED_WINDOW_DECORATIONS = "server";
    };
    dconf.settings."org/gnome/desktop/interface".color-scheme = "prefer-dark";
    fonts.fontconfig = {
      enable = true;
      antialiasing = true;
      subpixelRendering = "none";
      hinting = "full";
      defaultFonts = {
        monospace = [ "PragmataPro Mono Liga" ];
        sansSerif = [ "PragmataPro Liga" ];
      };
    };
    gtk = {
      enable = true;
      colorScheme = "dark";
      font = {
        name = "PragmataPro Liga";
        size = 8;
      };
      theme = {
        name = "Flat-Remix-GTK-Red-Darkest-Solid";
        package = pkgs.flat-remix-gtk;
      };
      iconTheme = {
        name = "Flat-Remix-Red-Dark";
        package = pkgs.flat-remix-icon-theme;
      };
    };
    qt = {
      enable = true;
      platformTheme.name = "gtk3";
    };
    services.ssh-agent.enable = true;
  };

  systemConfigs = {
    wsl = {
      home = {
        packages = with pkgs; [ wslu ];
        sessionVariables = {
          BROWSER = "wslview";
          EDITOR = "nvim";
          VISUAL = "nvim";
        };
      };
    };
    bare = {
      home = {
        packages = with pkgs; [
          blueman
          brave
          brightnessctl
          coppwr
          drm_info
          firefox
          obs-studio
          lxqt.pavucontrol-qt
        ];
        sessionVariables = {
          BROWSER = "brave";
          EDITOR = "zeditor --wait";
          VISUAL = "zeditor --wait";
        };
      };
    };
  };

  desktopConfig = lib.mkIf desktop {
    home.packages =
      with pkgs;
      [
        clip
        jetbrains.idea-ultimate
        legcord
        pcmanfm
        spotify
        xwayland-satellite
        wl-clipboard-rs
        zoom-us
      ]
      ++ (if system == "x86_64-linux" then [ slack ] else [ slacky ]);
    programs = {
      alacritty = {
        enable = true;
        theme = "tomorrow_night_bright";
        settings = {
          font = {
            normal.family = "PragmataPro Mono Liga";
            size = 8;
          };
        };
      };
      fuzzel = {
        enable = true;
        settings = {
          main = {
            dpi-aware = false;
            font = "PragmataPro Mono Liga:size=8";
            show-actions = true;
            horizontal-pad = 0;
            vertical-pad = 0;
          };
          border.radius = 0;
          colors = {
            background = "000000a0";
            border = "ff0000a0";
            input = "ffffffff";
            prompt = "ffffffff";
            selection = "ff0000ff";
            selection-match = "ffffffff";
            selection-text = "000000ff";
            text = "ffffffff";
          };
        };
      };
      swaylock = {
        enable = true;
        settings = {
          color = "000000";
        };
      };
      waybar = {
        enable = true;
        style = ''
          * {
            font-family: "PragmataPro Liga";
            font-size: 8pt;
          }
        '';
        settings = {
          mainBar = {
            spacing = 16;
            modules-left = [
              "tray"
              "cpu"
              "memory"
              "temperature"
            ];
            modules-center = [ "niri/window" ];
            modules-right = [
              "network"
              "wireplumber"
              "battery"
              "clock"
            ];
            clock.format = "{:%F %H:%M}";
            network.format = "{essid} {signaldBm}";
          };
        };
      };
    };
    services = {
      mako = {
        enable = true;
        settings = {
          font = "PragmataPro Liga 8";
          default-timeout = 1;
          background-color = "#000000a0";
          border-color = "#ff0000";
          border-size = 1;
        };
      };
      playerctld.enable = true;
      cliphist.enable = true;
    };
    xdg = {
      portal = {
        enable = true;
        config.common = {
          default = "gtk";
          "org.freedesktop.impl.portal.Screenshot" = "gnome";
          "org.freedesktop.impl.portal.ScreenCast" = "gnome";
        };
        extraPortals = with pkgs; [
          xdg-desktop-portal-gtk
          xdg-desktop-portal-gnome
        ];
      };
      configFile = {
        "niri/config.kdl".source = ./niri.kdl;
      };
    };
    home.pointerCursor = {
      enable = true;
      package = (pkgs.fuchsia-cursor.override { themeVariants = [ "Fuchsia-Red" ]; });
      name = "Fuchsia-Red";
      size = 24;
    };
  };

  trustedConfig = lib.mkIf trusted {
    home.packages = with pkgs; [
      _1password-gui
      element-desktop
      signal-desktop
    ];
    programs = {
      keychain = {
        enable = true;
        enableFishIntegration = true;
        keys = [ "id_ed25519" ];
      };
      git.signing = {
        format = "ssh";
        key = "~/.ssh/id_ed25519";
        signByDefault = true;
      };
    };
  };

  programsConfig = {
    programs = {
      zed-editor = {
        enable = true;
        installRemoteServer = true;
        userSettings = builtins.fromJSON (builtins.readFile ./zed-settings.json);
        extensions = [
          "css"
          "dockerfile"
          "git-firefly"
          "html"
          "kotlin"
          "nix"
          "protod"
          "svelte"
          "terraform"
          "toml"
          "tomorrow-theme"
          "zig"
        ];
      };
      claude-code.enable = true;
      delta = {
        enable = true;
        enableGitIntegration = true;
      };
      direnv.enable = true;
      eza = {
        enable = true;
        enableFishIntegration = true;
      };
      fzf = {
        enable = true;
        enableFishIntegration = true;
      };
      aria2.enable = true;
      fd.enable = true;
      gh = {
        enable = true;
        settings.git_protocol = "ssh";
      };
      neovim =
        let
          tomorrow = pkgs.vimUtils.buildVimPlugin {
            pname = "tomorrow-theme";
            version = "master";
            src = "${tomorrowTheme}/vim";
          };
        in
        {
          enable = true;
          viAlias = true;
          vimAlias = true;
          extraLuaConfig = ''
            vim.opt.autoindent = true
            vim.opt.smartindent = true
            vim.opt.expandtab = true
            vim.opt.number = true
            vim.opt.relativenumber = true
            vim.opt.cursorline = true
            vim.opt.colorcolumn = "100"
            vim.opt.termguicolors = true

            require('supermaven-nvim').setup({})

            vim.api.nvim_create_autocmd("BufWritePre", {
              callback = function(args)
                vim.lsp.buf.format({
                  bufnr = args.buf,
                  async = false,
               })
              end,
            })
            vim.cmd.colorscheme('Tomorrow-Night-Bright')
          '';
          plugins = with pkgs.vimPlugins; [
            tomorrow
            nvim-lspconfig
            vim-nix
            supermaven-nvim
            nvim-treesitter.withAllGrammars
          ];
        };
      nh = {
        enable = true;
        clean.enable = true;
        flake = "/src/sys";
      };
      ssh = {
        enable = true;
        enableDefaultConfig = false;
        extraConfig = ''
          StrictHostKeyChecking accept-new
        '';
        matchBlocks = {
          "*" = {
            addKeysToAgent = "yes";
            compression = true;
            controlMaster = "auto";
            controlPath = "~/.ssh/control-%r@%h:%p";
            controlPersist = "1h";
            serverAliveInterval = 60;
          };
          ident = {
            hostname = "ident.me";
            port = 2222;
            user = "root";
          };
          tnedi = {
            hostname = "tnedi.me";
            port = 2222;
            user = "root";
          };
          srvus = {
            hostname = "srv.us";
            port = 2222;
          };
          x0 = {
            hostname = "x0.xmit.dev";
            port = 2222;
            user = "root";
          };
          x1 = {
            hostname = "x1.xmit.dev";
            port = 2222;
            user = "root";
          };
          x2 = {
            hostname = "x2.xmit.dev";
            port = 2222;
            user = "root";
          };
          horse = {
            hostname = "horse.pcarrier.com";
          };
          gorilla = {
            forwardAgent = true;
          };
          komodo = {
            forwardAgent = true;
          };
          monster = {
            forwardAgent = true;
          };
        };
      };
      git = {
        enable = true;
        package = pkgs.gitFull;
        lfs.enable = true;
        settings = {
          user = {
            name = "Pierre Carrier";
            email = "pc@rrier.fr";
          };
          pull.rebase = true;
          init.defaultBranch = "main";
          github.user = "pcarrier";
          color.ui = true;
          branch = {
            autoSetupRebase = "always";
            sort = "-committerdate";
          };
          column.ui = "auto";
          status.showUntrackedFiles = "normal";
          gc.auto = 0;
          log.date = "iso";
          fetch = {
            prune = true;
            pruneTag = true;
            all = true;
          };
          push = {
            default = "simple";
            autoSetupRemote = true;
            followTags = true;
          };
          rerere = {
            enabled = true;
            autoupdate = true;
          };
          rebase = {
            autosquash = true;
            autostash = true;
            updaterefs = true;
          };
          diff = {
            algorithm = "histogram";
            mnemonicPrefix = true;
            renames = true;
            noprefix = true;
          };
          grep.fullname = true;
          tag.sort = "version:refname";
          commit.verbose = true;
          help = {
            autocorrect = "prompt";
            format = "web";
          };
          alias = {
            aamend = "commit -av --amend --no-edit";
            amend = "commit -av --amend";
            b = "branch -v";
            bl = "blame -C -C -C";
            bu = "bundle";
            cl = "clone";
            cm = "commit -av";
            co = "checkout";
            cp = "cherry-pick";
            cu = "rebase master";
            d = "diff --patience";
            dc = "describe --contains";
            ds = "diff --staged --patience";
            dsc = "describe";
            dw = "diff --color-words --patience";
            ec = "config --global -e";
            fp = "push --force-with-lease";
            k = "log --graph --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%ar) %C(bold blue)<%an>%Creset' --abbrev-commit --date=relative";
            kk = "log --no-merges --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%ar) %C(bold blue)<%an>%Creset' --abbrev-commit --date=relative";
            nr = "name-rev --name-only --refs=refs/heads/*";
            nri = "name-rev --refs=refs/heads/* --stdin";
            pl = "pull";
            p = "push";
            pr = "!gh pr create -w";
            pulla = "pull --all";
            pusha = "push --all";
            ra = "rebase --abort";
            rc = "rebase --continue";
            ri = "rebase --interactive";
            sl = "shortlog -sn";
            ss = "status -sbuno";
            sss = "status -sb";
            st = "status";
            wd = "diff --word-diff --patience";
          };
        };
      };
      lazygit = {
        enable = true;
        settings = {
          git.overrideGpg = true;
        };
      };
      fish = {
        enable = true;
        interactiveShellInit = ''
          set fish_greeting

          function __fish_postexec --on-event fish_postexec
            set_color yellow
            echo took $CMD_DURATION ms
            set_color normal
          end
        '';
        shellAliases = {
          a = "${pkgs.codex}/bin/codex --model gpt-5-codex --full-auto --search";
          b = "${pkgs.claude-code}/bin/claude --dangerously-skip-permissions";
          C = "clear";
          c = "code";
          ca = "cargo";
          dl = "aria2c -x 16 -s 16 -j 16";
          g = "git";
          lg = "lazygit";
          m = "ssh -t gorilla 'cd /src/monorepo; and exec fish -l'";
          mk = "ssh -t komodo 'cd /src/monorepo; and exec fish -l'";
          n = "nh os switch --accept-flake-config";
          t = "tmux attach";
          v = "nvim";
          km = "kubectl --context minikube";
          ks = "kubectl --namespace sandbox --context gke_twin-multiverse-sandbox_europe-west9_twin-multiverse-sandbox-paris";
          kpp = "kubectl --namespace prod --context gke_twin-multiverse-prod_europe-west9_twin-multiverse-prod-paris";
          kpi = "kubectl --namespace prod --context gke_twin-multiverse-prod_us-central1_twin-multiverse-prod-iowa";
          kps = "kubectl --namespace prod --context gke_twin-multiverse-prod_asia-southeast1_twin-multiverse-prod-singapore";
          zed = "zeditor";
        };
        functions = {
          T.body = "$argv 2>&1 | ts";
          cm.body = ''g cm -m "$argv"'';
          mr.body = ''
            rsync -avP --delete-after \
            --exclude /.git \
            --exclude .direnv \
            --exclude target \
            --exclude node_modules \
            --exclude .terraform \
            --exclude __pycache__ \
            /src/monorepo/ gorilla:/src/monorepo/; and ssh gorilla "cd $PWD; and exec direnv exec . fish -lic '$argv'"
          '';
          mrk.body = ''
            rsync -avP --delete-after \
            --exclude /.git \
            --exclude .direnv \
            --exclude target \
            --exclude node_modules \
            --exclude .terraform \
            --exclude __pycache__ \
            /src/monorepo/ komodo:/src/monorepo/; and ssh gorilla "cd $PWD; and exec direnv exec . fish -lic '$argv'"
          '';
          nu.body = ''
            set -l ref (git -C /src/sys rev-parse HEAD)
            for host in $argv
              echo === $host ===
              ssh $host nh os switch github:pcarrier/sys/$ref --accept-flake-config
            end
          '';
          nuke.body = ''
            set -l ref (git -C /src/sys rev-parse HEAD)
            for host in $argv
              echo === $host ===
              ssh root@$host nixos-rebuild switch github:pcarrier/sys#$ref
            end
          '';
        };
      };
      tmux = {
        enable = true;
        historyLimit = 1000000;
        mouse = true;
        newSession = true;
        clock24 = true;
      };
      zoxide = {
        enable = true;
        enableFishIntegration = true;
      };
    };
  };
in
{
  home-manager = {
    backupFileExtension = "backup";
    useGlobalPkgs = true;
    useUserPackages = true;
    users = {
      pcarrier = lib.mkMerge [
        baseConfig
        (systemConfigs.${systemType} or { })
        desktopConfig
        trustedConfig
        programsConfig
      ];
    };
  };
}
