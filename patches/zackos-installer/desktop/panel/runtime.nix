{ pkgs ? import <nixpkgs> {} }:
let
  python = pkgs.python3.withPackages (p: [ p.pygobject3 p.pycairo p.i3ipc p.python-xlib ]);
  typelibs = pkgs.lib.makeSearchPath "lib/girepository-1.0" (map pkgs.lib.getLib (with pkgs; [
    gtk3 glib gobject-introspection gdk-pixbuf pango harfbuzz at-spi2-core cairo
  ]));
in
pkgs.writeShellScriptBin "zackos-panel" ''
  export GI_TYPELIB_PATH="${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
  export XDG_DATA_DIRS="/nix/var/nix/profiles/default/share:''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
  export XDG_CURRENT_DESKTOP="''${XDG_CURRENT_DESKTOP:-i3}"
  if [ "''${1:-}" = launcher ] && ${pkgs.lib.getBin pkgs.glib}/bin/gapplication action org.zackos.Panel launcher 2>/dev/null; then
    exit 0
  fi
  exec ${python}/bin/python3 /usr/local/lib/zackos-installer/desktop/panel/main.py "$@"
''
