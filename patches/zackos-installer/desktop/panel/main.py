"""ZackOS's i3 panel and application launcher."""
from pathlib import Path
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, Gtk
import i3ipc
from Xlib import X
from Xlib.display import Display

from launcher import Launcher
from panelbar import Panel, SPACES, SystemStats


def socket_path(display):
    # i3 exports I3SOCK to children once, but may choose a new path on restart.
    atom = display.intern_atom("I3_SOCKET_PATH")
    prop = display.screen().root.get_full_property(atom, X.AnyPropertyType)
    if not prop or not prop.value:
        raise ConnectionError("i3 has not published its IPC socket")
    return prop.value.decode()


class DesktopPanel(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.zackos.Panel", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.panels = {}
        self.geometries = {}
        self.mode = "default"
        self.ready = False
        self.stopping = threading.Event()
        self.connection = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # Keep listening when the last monitor is temporarily unplugged.
        action = Gio.SimpleAction.new("launcher", None)
        action.connect("activate", lambda _action, _param: self.toggle_focused())
        self.add_action(action)

    def do_activate(self):
        if self.ready:
            return
        self.ready = True
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("panel.css")))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.x11 = Display()
        self.stats = SystemStats()
        self.launcher = Launcher()
        self.refresh()
        self.update_stats()
        GLib.timeout_add_seconds(2, self.update_stats)
        threading.Thread(target=self.watch_i3, daemon=True).start()

    def current_ipc(self):
        path = socket_path(self.x11)
        if self.connection is None or self.connection.socket_path != path:
            self.connection = i3ipc.Connection(socket_path=path, auto_reconnect=True)
        return self.connection

    def do_command_line(self, command):
        args = command.get_arguments()[1:]
        if args not in ([], ["launcher"]):
            return 2
        self.activate()
        if args:
            self.toggle_focused()
        return 0

    def toggle_focused(self):
        self.activate()
        try:
            spaces = self.current_ipc().get_workspaces()
        except (OSError, EOFError):
            return
        active = next((w.output for w in spaces if w.focused), None)
        if self.panels:
            self.toggle_launcher(self.panels.get(active) or next(iter(self.panels.values())))

    def watch_i3(self):
        def changed(_ipc, event):
            if hasattr(event, "pango_markup"):
                GLib.idle_add(self.set_mode, event.change)
            else:
                GLib.idle_add(self.refresh)

        def shutdown(_ipc, event):
            if event.change == "exit":
                GLib.idle_add(self.quit)

        display = Display()
        try:
            while not self.stopping.is_set():
                try:
                    stream = i3ipc.Connection(socket_path=socket_path(display))
                    for kind in (i3ipc.Event.WORKSPACE, i3ipc.Event.OUTPUT, i3ipc.Event.MODE,
                                 i3ipc.Event.WINDOW_FOCUS, i3ipc.Event.WINDOW_TITLE, i3ipc.Event.WINDOW_CLOSE):
                        stream.on(kind, changed)
                    stream.on(i3ipc.Event.SHUTDOWN, shutdown)
                    GLib.idle_add(self.refresh)
                    stream.main()
                except (OSError, EOFError):
                    pass
                self.stopping.wait(.5)
        finally:
            display.close()

    def set_mode(self, mode):
        self.mode = mode
        for bar in self.panels.values():
            bar.mode.set_text(mode)
            bar.mode.set_visible(mode != "default")
        return False

    def refresh(self):
        try:
            ipc = self.current_ipc()
            spaces = ipc.get_workspaces()
            outputs = [o for o in ipc.get_outputs() if o.active and o.name != "__i3"]
            focused = ipc.get_tree().find_focused()
        except (OSError, EOFError):
            self.connection = None
            return False
        screen = Gdk.Screen.get_default()
        scales = {screen.get_monitor_plug_name(n): screen.get_monitor_scale_factor(n)
                  for n in range(screen.get_n_monitors())}
        geometries = {o.name: (o.rect.x, o.rect.y, o.rect.width, o.rect.height, scales.get(o.name, 1))
                      for o in outputs}
        for name in list(self.panels):
            if self.geometries[name] != geometries.get(name):
                bar = self.panels.pop(name)
                if self.launcher.panel is bar:
                    self.launcher.close_menu()
                    self.launcher.panel = None
                bar.destroy()
        self.geometries = geometries
        by_number = {ws.num: ws.name for ws in spaces}
        defaults = [by_number.get(n, f"{n}:{title}") for n, title in enumerate(SPACES, 1)]
        selected_output = next((w.output for w in spaces if w.focused), None)
        for output in outputs:
            if output.name not in self.panels:
                bar = Panel(self, output, scales.get(output.name, 1))
                self.panels[output.name] = bar
                self.add_window(bar)
                bar.show_all()
            bar = self.panels[output.name]
            title = ""
            if bar.bounds[2] >= 1050 and output.name == selected_output and focused and focused.window:
                title = focused.name or ""
            bar.focus_title.set_text(title)
            bar.focus_title.set_tooltip_text(title or None)
            local = [ws for ws in spaces if ws.output == output.name]
            active = next((ws.name for ws in local if ws.visible), None)
            extra = [ws.name for ws in local if ws.name not in defaults]
            bar.spaces.update(defaults + extra, active, {ws.name for ws in spaces if ws.urgent})
        self.set_mode(self.mode)
        return False

    def update_stats(self):
        load, memory = self.stats.read()
        for bar in self.panels.values():
            bar.update_stats(load, memory)
        return True

    def switch_workspace(self, name):
        # Use the exact name i3 reported, including user-defined names and punctuation.
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        try:
            self.current_ipc().command(f'workspace --no-auto-back-and-forth "{escaped}"')
        except (OSError, EOFError):
            self.connection = None

    def toggle_launcher(self, bar):
        if self.launcher.opened and self.launcher.panel is bar:
            self.launcher.close_menu()
        else:
            self.launcher.open_menu(bar)

    def do_shutdown(self):
        self.stopping.set()
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    sys.exit(DesktopPanel().run(sys.argv))
