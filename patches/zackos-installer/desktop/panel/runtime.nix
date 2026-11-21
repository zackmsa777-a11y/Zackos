{ pkgs ? import <nixpkgs> {} }:
let
  gtk = import ../gtk-python.nix { inherit pkgs; };
in
pkgs.writeShellScriptBin "zackos-panel" ''
  if [ "''${1:-}" = launcher ] && ${pkgs.lib.getBin pkgs.glib}/bin/gapplication action org.zackos.Panel launcher 2>/dev/null; then
    exit 0
  fi
  exec ${gtk}/bin/zackos-gtk-python /usr/local/lib/zackos-installer/desktop/panel/main.py "$@"
''
