#!/bin/sh
# Both LightDM and startx use this Plasma session.
profile=/nix/var/nix/profiles/default
export PATH="$profile/bin:/usr/local/bin:/usr/bin:/bin"
export XDG_DATA_DIRS="$profile/share:/usr/local/share:/usr/share"
export XDG_CONFIG_DIRS="/etc/xdg:$profile/etc/xdg"
export QT_PLUGIN_PATH="$profile/lib/qt-6/plugins"
export QML2_IMPORT_PATH="$profile/lib/qt-6/qml"
export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=KDE
if [ "$#" -eq 0 ]; then set -- "$profile/bin/startplasma-x11"; fi
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    XDG_RUNTIME_DIR=$(mktemp -d) || exit 1
    export XDG_RUNTIME_DIR
    trap 'rm -rf -- "$XDG_RUNTIME_DIR"' EXIT
    dbus-run-session -- "$@"
else
    exec dbus-run-session -- "$@"
fi
