"""A searchable app menu for the active panel, backed by XDG desktop entries."""
import math

import cairo
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from panelbar import Glyph, HEIGHT, Motion, label, styled

PREFERRED = ("org.zackos.Installer.desktop", "firefox.desktop", "thunar.desktop", "kitty.desktop")
FALLBACK = {
    "firefox.desktop": ("Web browser", "web-browser-symbolic", "web"),
    "thunar.desktop": ("File manager", "folder", "files"),
    "thunar-bulk-rename.desktop": ("Rename files", "edit", "edit"),
    "thunar-settings.desktop": ("File manager settings", "settings", "settings"),
    "rxvt-unicode.desktop": ("Terminal emulator", "terminal", "term"),
    "kitty.desktop": ("ZackOS terminal", "utilities-terminal", "term"),
    "xterm.desktop": ("Terminal emulator", "terminal", "term"),
}


def app_details(app):
    name = app.get_name()
    generic, icon, tint = FALLBACK.get(app.get_id(), ("", "application-x-executable-symbolic", "other"))
    if app.get_id() == "thunar.desktop" and name == "Thunar File Manager":
        name = "Thunar"
    subtitle = generic or getattr(app, "get_generic_name", lambda: "")() or app.get_description() or ""
    if subtitle.casefold() == name.casefold():
        subtitle = app.get_description() or ""
    return name, subtitle, icon, tint


def shape(width, height, radius):
    """Round the popup and clip its reveal on X11, including uncomposited displays."""
    radius = min(radius, height // 2, width // 2)
    region = cairo.Region(cairo.RectangleInt(0, radius, width, max(1, height - 2*radius)))
    for y in range(radius):
        inset = math.ceil(radius - math.sqrt(radius*radius - (radius-y-.5)**2))
        for row in (y, height - y - 1):
            region.union(cairo.RectangleInt(inset, row, width-2*inset, 1))
    return region


class Launcher(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_title("ZackOS Applications")
        self.set_wmclass("zackos-launcher", "ZackOSLauncher")
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self.outside_click)
        self.connect("key-press-event", self.key_pressed)
        self.motion = Motion(self)
        self.progress = 0
        self.opened = False
        self.panel = None
        self.catalog = []
        self.dirty = True
        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect("changed", self.catalog_changed)
        self.selected = 0
        self.cards, self.results = [], []
        self.cols = 2

        outer = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "launcher")
        self.add(outer)
        heading = styled(Gtk.Box(spacing=10), "heading")
        outer.add(heading)
        title = label("Applications", "title")
        title.set_xalign(0)
        heading.pack_start(title, True, True, 0)
        escape = styled(Gtk.Button(label="Esc"), "keycap")
        escape.set_valign(Gtk.Align.CENTER)
        escape.set_tooltip_text("Close applications")
        escape.connect("clicked", lambda _button: self.close_menu())
        heading.add(escape)

        self.search = styled(Gtk.SearchEntry(), "search")
        search_area = Gtk.Overlay()
        search_area.add(self.search)
        self.placeholder = label("Search applications", "placeholder")
        self.placeholder.set_halign(Gtk.Align.START)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.placeholder.set_no_show_all(True)
        search_area.add_overlay(self.placeholder)
        search_area.set_overlay_pass_through(self.placeholder, True)
        self.search.get_accessible().set_name("Search applications")
        self.search.connect("changed", lambda _entry: self.filter())
        self.search.connect("activate", lambda _entry: self.launch_selected())
        outer.add(search_area)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_overlay_scrolling(False)
        outer.pack_start(self.scroll, True, True, 0)
        self.grid = styled(Gtk.Grid(column_spacing=7, row_spacing=7), "app-grid")
        self.grid.set_column_homogeneous(True)
        self.grid.set_valign(Gtk.Align.START)
        self.scroll.add(self.grid)
        footer = styled(Gtk.Box(spacing=14), "footer")
        self.count = label("", "count")
        footer.pack_start(self.count, True, True, 0)
        self.count.set_xalign(0)
        footer.add(label("↑ ↓  Navigate", "hint"))
        footer.add(label("↵  Open", "hint"))
        outer.add(footer)

    def catalog_changed(self, _monitor):
        self.dirty = True
        if self.opened:
            self.filter()

    def filter(self):
        if self.dirty:
            self.catalog = []
            for app in Gio.AppInfo.get_all():
                if not app.should_show():
                    continue
                name, subtitle, icon, tint = app_details(app)
                keywords = getattr(app, "get_keywords", lambda: [])() or []
                tokens = " ".join((name, subtitle, app.get_executable() or "", *keywords)).casefold()
                self.catalog.append((app, name, subtitle, icon, tint, tokens))
            self.catalog.sort(key=lambda row: (
                PREFERRED.index(row[0].get_id()) if row[0].get_id() in PREFERRED else len(PREFERRED),
                row[1].casefold()))
            self.dirty = False
        self.placeholder.set_visible(not self.search.get_text())
        terms = self.search.get_text().casefold().split()
        self.results = [row for row in self.catalog if all(t in row[-1] for t in terms)]
        for widget in self.grid.get_children():
            widget.destroy()
        self.cards = []
        self.selected = 0
        for index, (app, name, subtitle, fallback, tint, _) in enumerate(self.results):
            item = styled(Gtk.Button(), "app-card")
            item.get_accessible().set_name("Launch " + name)
            item.set_tooltip_text(app.get_description() or name)
            item.connect("clicked", lambda _item, chosen=app: self.launch(chosen))
            content = Gtk.Box(spacing=10)
            badge = styled(styled(Gtk.Box(), "icon-tile"), tint)
            badge.set_halign(Gtk.Align.CENTER)
            badge.set_valign(Gtk.Align.CENTER)
            gicon = app.get_icon()
            theme = Gtk.IconTheme.get_default()
            if fallback in ("terminal", "folder", "settings", "edit"):
                image = Glyph(fallback, 25)
            elif gicon and theme.lookup_by_gicon(gicon, 26, Gtk.IconLookupFlags.USE_BUILTIN):
                image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.LARGE_TOOLBAR)
                image.set_pixel_size(25)
            else:
                image = Glyph("grid", 25)
            badge.add(image)
            content.add(badge)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.set_valign(Gtk.Align.CENTER)
            for string, css in ((name, "app-name"), (subtitle, "subtitle")):
                if string:
                    line = label(string, css)
                    line.set_xalign(0)
                    line.set_ellipsize(Pango.EllipsizeMode.END)
                    line.set_max_width_chars(19)
                    text.pack_start(line, False, False, 0)
            content.pack_start(text, True, True, 0)
            item.add(content)
            self.grid.attach(item, index % self.cols, index // self.cols, 1, 1)
            self.cards.append(item)
        if not self.cards:
            self.grid.attach(label("No applications found", "empty"), 0, 0, self.cols, 1)
        total = len(self.results)
        self.count.set_text(f"{total} application" + ("" if total == 1 else "s"))
        self.grid.show_all()
        self.scroll.get_vadjustment().set_value(0)
        self.choose(0)

    def choose(self, index):
        if not self.cards:
            return
        self.cards[self.selected].get_style_context().remove_class("chosen")
        self.selected = max(0, min(index, len(self.cards)-1))
        card = self.cards[self.selected]
        card.get_style_context().add_class("chosen")
        pos = card.translate_coordinates(self.grid, 0, 0)
        if pos:
            self.scroll.get_vadjustment().clamp_page(pos[1], pos[1] + card.get_allocated_height())

    def launch_selected(self):
        if self.results:
            self.launch(self.results[self.selected][0])

    def launch(self, app):
        context = self.get_display().get_app_launch_context()
        context.set_timestamp(Gtk.get_current_event_time())
        try:
            app.launch([], context)
        except GLib.Error as error:
            self.count.set_text("Could not open " + app.get_name())
            self.count.set_tooltip_text(error.message)
            return
        self.close_menu()

    def key_pressed(self, _window, event):
        key = event.keyval
        shortcut = event.state & (Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.SUPER_MASK)
        if key == Gdk.KEY_Escape or (shortcut and key in (Gdk.KEY_d, Gdk.KEY_D)):
            self.close_menu()
        elif key in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.choose(self.selected + (self.cols if key == Gdk.KEY_Down else -self.cols))
            self.search.grab_focus()
        elif key in (Gdk.KEY_Right, Gdk.KEY_Left) and not self.search.get_text():
            self.choose(self.selected + (1 if key == Gdk.KEY_Right else -1))
        else:
            return False
        return True

    def outside_click(self, _window, event):
        x, y = self.get_position()
        width, height = self.get_size()
        if not (x <= event.x_root < x+width and y <= event.y_root < y+height):
            self.close_menu()
            return True
        return False

    def set_progress(self, progress):
        self.progress = progress
        width, height = self.get_size()
        visible = max(1, round(height*progress))
        self.get_window().shape_combine_region(shape(width, visible, 14), 0, 0)

    def open_menu(self, panel):
        if self.panel:
            self.panel.apps.get_style_context().remove_class("open")
            self.panel.apps.set_has_tooltip(True)
        self.panel = panel
        panel.apps.set_has_tooltip(False)
        panel.apps.get_style_context().add_class("open")
        self.set_transient_for(panel)
        x, y, width, height = panel.bounds
        self.cols = 2 if width >= 475 else 1
        # Reserve room for the panel and keep the last row reachable on small outputs.
        self.move(x+8, y+HEIGHT+4)
        self.search.set_text("")
        self.count.set_tooltip_text(None)
        self.filter()
        rows = min(5, max(2, (len(self.catalog)+self.cols-1)//self.cols))
        self.set_size_request(min(512, width-16), min(150 + rows*63, height-HEIGHT-12))
        self.opened = True
        self.realize()
        self.set_progress(0)
        self.show_all()
        self.search.grab_focus()
        self.get_display().get_default_seat().grab(
            self.get_window(), Gdk.SeatCapabilities.ALL_POINTING | Gdk.SeatCapabilities.KEYBOARD,
            True, None, None, None)
        self.motion.run(230, self.set_progress)

    def close_menu(self):
        if not self.opened:
            return
        self.opened = False
        self.get_display().get_default_seat().ungrab()
        self.panel.apps.get_style_context().remove_class("open")
        self.panel.apps.set_has_tooltip(True)
        initial = self.progress
        self.motion.run(150, lambda f: self.set_progress(initial*(1-f)), self.hide)
