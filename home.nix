{
  pkgs,
  lib,
  systemType,
  baze,
  tomorrowThemeSrc,
  trusted ? false,
  desktop ? false,
  ...
}:
let
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
        fd
        fastfetch
        ffmpeg
        file
        gnuplot
        htop
        jq
        ldns
        moreutils
        mosh
        nixd
        nixfmt
        nmap
        nodejs
        pssh
        ripgrep
        yt-dlp
        wget # for remote vscode
        zoxide
      ];
      sessionVariables = {
        EDITOR = "code --wait";
        VISUAL = "code --wait";
      };
    };
  };

  systemConfigs = {
    wsl = {
      home = {
        packages = with pkgs; [ wslu ];
        sessionVariables.BROWSER = "wslview";
        sessionPath = [
          "/mnt/c/Users/pierr/AppData/Local/Programs/Microsoft VS Code/bin"
        ];
      };
      programs.fish.functions.pbcopy = {
        body = "clip.exe";
      };
    };
    bare = {
      home = {
        packages = with pkgs; [
          brave
          kdePackages.krdc
          simplescreenrecorder
          xclip
        ];
        sessionVariables.BROWSER = "brave";
      };
      programs = {
        vscode = {
          enable = true;
          profiles.default.extensions = with pkgs.vscode-extensions; [
            # openai.chatgpt missing
            ms-azuretools.vscode-containers
            mkhl.direnv
            ms-azuretools.vscode-docker
            editorconfig.editorconfig
            tamasfe.even-better-toml
            github.vscode-github-actions
            github.vscode-pull-request-github
            eamodio.gitlens
            golang.go
            hashicorp.terraform
            oderwat.indent-rainbow
            jnoortheen.nix-ide
            esbenp.prettier-vscode
            rust-lang.rust-analyzer
            supermaven.supermaven
          ];
        };
        fish.functions.pbcopy = {
          body = "xclip -selection clipboard";
        };
      };
    };
  };

  desktopConfig = lib.mkIf desktop {
    home.packages = with pkgs; [
      _1password-gui
      slack
      spotify
      zoom-us
    ];
  };

  trustedConfig = lib.mkIf trusted {
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
            src = "${tomorrowThemeSrc}/vim";
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
            local lsp = require('lspconfig')
            lsp.nixd.setup({})
            lsp.rust_analyzer.setup({})

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
        };
      };
      git = {
        enable = true;
        userName = "Pierre Carrier";
        userEmail = "pc@rrier.fr";
        lfs.enable = true;
        delta.enable = true;
        extraConfig = {
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
        };
        aliases = {
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
      lazygit.enable = true;
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
          C = "clear";
          c = "code";
          dl = "aria2c -x 16 -s 16 -j 16";
          g = "git";
          lg = "lazygit";
          n = "nh os switch";
          t = "tmux attach";
          v = "nvim";
          km = "kubectl --context minikube";
          ks = "kubectl --namespace sandbox --context gke_twin-multiverse-sandbox_europe-west9_twin-multiverse-sandbox-paris";
          kpp = "kubectl --namespace prod --context gke_twin-multiverse-prod_europe-west9_twin-multiverse-prod-paris";
          kpi = "kubectl --namespace prod --context gke_twin-multiverse-prod_us-central1_twin-multiverse-prod-iowa";
        };
        functions = {
          T = {
            body = "$argv 2>&1 | ts";
          };
          cm = {
            body = ''git cm -m "$argv"'';
          };
          nu = {
            body = ''
              set -l ref (git -C /src/sys rev-parse HEAD)
              for host in $argv
                echo === $host ===
                ssh $host nh os switch github:pcarrier/sys/$ref
              end
            '';
          };
          nuke = {
            body = ''
              set -l ref (git -C /src/sys rev-parse HEAD)
              for host in $argv
                echo === $host ===
                ssh root@$host nixos-rebuild switch github:pcarrier/sys#$ref
              end
            '';
          };
          rc = {
            body = ''
              cursor --folder-uri=vscode-remote://ssh-remote+$1(pwd)
            '';
          };
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
