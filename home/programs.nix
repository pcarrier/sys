{
  pkgs,
  lib,
  nixpkgs-master,
  system,
  tomorrowTheme,
  trusted ? false,
}:
let
  pkgs-master = import nixpkgs-master {
    inherit system;
    config.allowUnfree = true;
  };
  gitPackage = pkgs.gitFull;
in
lib.mkMerge [
  {
    programs = {
      zed-editor = {
        enable = true;
        installRemoteServer = true;
        userSettings = builtins.fromJSON (builtins.readFile ./dotfiles/zed-settings.json);
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
      claude-code = {
        enable = true;
        package = pkgs-master.claude-code;
      };
      delta = {
        enable = true;
        enableGitIntegration = true;
      };
      direnv.enable = true;
      mise.enable = true;
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
          initLua = ''
            vim.opt.autoindent = true
            vim.opt.smartindent = true
            vim.opt.expandtab = true
            vim.opt.number = true
            vim.opt.relativenumber = true
            vim.opt.cursorline = true
            vim.opt.colorcolumn = "100"
            vim.opt.termguicolors = true

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
          c0 = {
            hostname = "c0.xmit.dev";
            user = "root";
          };
          c1 = {
            hostname = "c1.xmit.dev";
            user = "root";
          };
          horse = {
            hostname = "horse.pcarrier.com";
          };
        };
      };
      git = {
        enable = true;
        package = gitPackage;
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
            autoSetupMerge = "simple";
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
        shellInit = ''
          fish_add_path --prepend ~/.nix-profile/bin /nix/var/nix/profiles/default/bin
        '';
        interactiveShellInit = ''
          set fish_greeting

          function __fish_postexec --on-event fish_postexec
            set_color yellow
            echo took $CMD_DURATION ms
            set_color normal
          end
        '';
        shellAliases = {
          a = "${pkgs-master.claude-code}/bin/claude --dangerously-skip-permissions";
          ac = "${pkgs-master.claude-code}/bin/claude --dangerously-skip-permissions --continue";
          i = "${pkgs.uv}/bin/uvx indent";
          C = "clear";
          c = "code";
          ca = "cargo";
          dl = "aria2c -x 16 -s 16 -j 16";
          g = "${gitPackage}/bin/git";
          git = "${gitPackage}/bin/git";
          lg = "lazygit";
          m = "ssh -t gorilla 'cd /src/monorepo; and exec fish -l'";
          mk = "ssh -t komodo 'cd /src/monorepo; and exec fish -l'";
          n = if pkgs.stdenv.isDarwin then "nh darwin switch --accept-flake-config ~/src/sys" else "nh os switch --accept-flake-config";
          t = "tmux attach";
          v = "nvim";
          zed = "zeditor";
        };
        functions = {
          T.body = "$argv 2>&1 | ts";
          cm.body = ''g cm -m "$argv"'';
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
  }
  (lib.mkIf trusted {
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
      gpg.enable = true;
    };
  })
]
