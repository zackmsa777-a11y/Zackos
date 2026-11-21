"""ZackOS graphical installer for the live i3 session."""
import json
import math
from pathlib import Path
import socket
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

HERE = Path(__file__).resolve().parent
STEPS = ("Welcome", "Destination", "Account", "Review")
DESKTOPS = {"i3": "i3 desktop", "none": "Console only", "plasma-x11": "KDE Plasma X11"}
INITS = {"runit": "runit", "sysvinit": "SysVinit"}


def request(action, **fields):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(4)
        sock.connect("/run/zackos-install.sock")
        sock.sendall((json.dumps({"action": action, **fields}) + "\n").encode())
        with sock.makefile("rb") as stream:
            result = json.loads(stream.readline(130000))
    if "error" in result:
        raise ValueError(result["error"])
    return result


def style(widget, name):
    widget.get_style_context().add_class(name)
    return widget


def column(spacing=0):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def label(text, name, wrap=False):
    w = style(Gtk.Label(label=text, xalign=0), name)
    w.set_line_wrap(wrap)
    w.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if wrap:
        w.set_max_width_chars(56)
    return w


def button(text, name, callback):
    w = style(Gtk.Button(label=text), name)
    w.connect("clicked", lambda _: callback())
    return w


def choices(values, initial):
    w = Gtk.ComboBoxText()
    for key, value in values.items():
        w.append(key, value)
    w.set_active_id(initial)
    return w


class Planet(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.art = GdkPixbuf.Pixbuf.new_from_file(str(HERE.parent / "desktop/orbit.png"))
        self.set_size_request(-1, 187)
        self.connect("draw", self.paint)

    def paint(self, area, cr):
        width, height = area.get_allocated_width(), area.get_allocated_height()
        for x, y, start in ((width-10, 10, -90), (width-10, height-10, 0),
                             (10, height-10, 90), (10, 10, 180)):
            cr.arc(x, y, 10, math.radians(start), math.radians(start+90))
        cr.close_path()
        cr.clip()
        # A detail of the desktop's orbital illustration, without its embedded wordmark.
        scale = max(width / 1000, height / 450)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, self.art, -(self.art.get_width()-width/scale),
                                    -(390-height/(2*scale)))
        cr.paint()
        return False


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Install ZackOS")
        self.set_wmclass("zackos-installer", "ZackInstaller")
        self.set_decorated(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        screen = Gdk.Screen.get_default()
        rect = screen.get_monitor_workarea(max(0, screen.get_primary_monitor()))
        self.set_default_size(min(920, rect.width-24), min(570, rect.height-24))
        self.page = 0
        self.disk = None
        self.phase = "ready"
        self.auto_at = None
        self.started_here = False
        self.fields = {}
        self.rail_steps = []
        shell = style(Gtk.Box(), "shell")
        self.add(shell)
        rail = style(column(4), "rail")
        rail.set_size_request(171, -1)
        shell.pack_start(rail, False, False, 0)
        brand = Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(HERE.parent / "branding/wordmark.png"), 143, 46, True))
        brand.set_halign(Gtk.Align.START)
        rail.add(style(brand, "brand"))
        for n, title in enumerate(STEPS, 1):
            row = style(Gtk.Box(spacing=9), "rail-step")
            number = style(Gtk.Label(label=str(n)), "rail-number")
            row.add(number)
            row.add(Gtk.Label(label=title))
            rail.add(row)
            self.rail_steps.append((row, number))
        live = Gtk.Box(spacing=9)
        live.add(label("●", "live-lamp"))
        info = column(2)
        info.add(label("Live session", "live-status"))
        info.add(label("live  ·  i3 desktop", "live-sub"))
        live.add(info)
        rail.pack_end(live, False, False, 3)

        right = column()
        shell.pack_start(right, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right.pack_start(scroll, True, True, 0)
        body = style(column(12), "body")
        scroll.add(body)
        self.scroll = scroll
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140)
        self.stack.set_homogeneous(False)
        body.pack_start(self.stack, True, True, 0)
        self.pages = [self.welcome(), self.destination(), self.account(), self.review(), self.progress_page()]
        for n, page in enumerate(self.pages):
            self.stack.add_named(page, str(n))
        self.notice = label("", "error", True)
        body.add(self.notice)
        footer = style(Gtk.Box(spacing=10), "footer")
        right.pack_end(footer, False, False, 0)
        self.back = button("Try ZackOS", "quiet", self.previous)
        self.forward = button("Choose a disk  →", "primary", self.next)
        footer.pack_start(self.back, False, False, 0)
        footer.pack_end(self.forward, False, False, 0)
        self.show_all()
        self.go(0)
        self.poll()
        self.poll_id = GLib.timeout_add_seconds(1, self.poll)
        self.connect("destroy", lambda *_: GLib.source_remove(self.poll_id))

    def heading(self, title, subtitle):
        box = column(7)
        box.add(label(title, "heading"))
        box.add(label(subtitle, "lede", True))
        return box

    def welcome(self):
        box = column(17)
        box.add(Planet())
        box.add(self.heading("Install ZackOS", "Set up ZackOS on your computer, or try the live desktop first."))
        facts = style(Gtk.Box(spacing=8), "fact-box")
        for n, (name, value) in enumerate((("Desktop", "i3 window manager"), ("Install source", "This live media"))):
            if n:
                facts.add(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            fact = column(4)
            fact.add(label(name, "details"))
            fact.add(label(value, "fact-value"))
            facts.pack_start(fact, True, True, 0)
        box.add(facts)
        return box

    def destination(self):
        box = column(12)
        box.add(self.heading("Choose a destination", "Select the whole disk where ZackOS will be installed."))
        top = Gtk.Box()
        top.pack_start(label("Available disks", "legend"), True, True, 0)
        top.add(button("Refresh", "quiet", self.load_disks))
        box.add(top)
        self.disklist = style(Gtk.ListBox(), "disk-list")
        self.disklist.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.disklist.connect("row-selected", self.disk_chosen)
        area = Gtk.ScrolledWindow()
        area.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        area.set_min_content_height(95)
        area.add(self.disklist)
        box.add(area)
        self.disk_hint = label("", "details", True)
        box.add(self.disk_hint)
        opts = Gtk.Box(spacing=14)
        self.desktop = choices(DESKTOPS, "i3")
        self.init = choices(INITS, "runit")
        for text, field in (("Desktop", self.desktop), ("Init system", self.init)):
            col = column(4)
            col.add(label(text, "legend"))
            col.add(field)
            opts.pack_start(col, True, True, 0)
        box.add(opts)
        self.source_hint = label("", "details", True)
        box.add(self.source_hint)
        for field in (self.desktop, self.init):
            field.connect("changed", self.source_changed)
        self.source_changed()
        return box

    def source_changed(self, *_):
        online = self.desktop.get_active_id() == "plasma-x11" or (self.desktop.get_active_id() == "none"
                                                               and self.init.get_active_id() == "runit")
        self.source_hint.set_text("This selection downloads packages and needs internet access." if online
                                  else "All packages for this selection are on the live media.")

    def load_disks(self):
        prior = self.disk
        for row in self.disklist.get_children():
            self.disklist.remove(row)
        self.disk = None
        try:
            disks = request("disks")["disks"]
        except (ValueError, OSError) as exc:
            self.notice.set_text(str(exc))
            disks = []
        for d in disks:
            row = Gtk.ListBoxRow()
            row.disk = d
            line = Gtk.Box(spacing=14)
            left = column(3)
            left.add(label(d["model"], "disk-name"))
            left.add(label(d["path"], "disk-path"))
            line.pack_start(left, True, True, 0)
            line.add(label(f'{d["bytes"]/1024**3:.1f} GiB', "disk-name"))
            row.add(line)
            self.disklist.add(row)
            if prior and prior["identity"] == d["identity"]:
                self.disklist.select_row(row)
        self.disklist.show_all()
        self.disk_hint.set_text("The selected disk will be erased after you confirm on the Review screen."
                                if disks else "No available disk. Attach a writable disk and choose Refresh.")
        self.forward.set_sensitive(self.disk is not None)

    def disk_chosen(self, _list, row):
        self.disk = row.disk if row else None
        if self.page == 1:
            self.forward.set_sensitive(self.disk is not None)

    def account(self):
        box = column(15)
        box.add(self.heading("Create your account", "Use your login password for sudo and the root password for su."))
        grid = Gtk.Grid(column_spacing=14, row_spacing=11)
        for index, (key, title, default, secret) in enumerate((
            ("username", "Username", "zack", False), ("hostname", "Computer name", "zackos", False),
            ("user_password", "Your password", "", True), ("user_confirm", "Confirm your password", "", True),
            ("root_password", "Root password", "", True), ("root_confirm", "Confirm root password", "", True))):
            field = Gtk.Entry(text=default)
            field.set_visibility(not secret)
            field.set_max_length(512 if secret else 253)
            field.set_hexpand(True)
            field.get_accessible().set_name(title)
            field.connect("activate", lambda _: self.next())
            col = column(4)
            col.add(label(title, "legend"))
            col.add(field)
            grid.attach(col, index % 2, index // 2, 1, 1)
            self.fields[key] = field
        box.add(grid)
        return box

    def review(self):
        box = column(15)
        box.add(self.heading("Review installation", "Check your choices before writing to disk."))
        self.summary = style(column(8), "review-box")
        box.add(self.summary)
        self.erase = Gtk.CheckButton()
        self.erase.connect("toggled", lambda w: self.forward.set_sensitive(w.get_active()) if self.page == 3 else None)
        box.add(self.erase)
        self.auto = Gtk.CheckButton(label="Restart into ZackOS when installation finishes")
        self.auto.set_active(True)
        box.add(self.auto)
        return box

    def update_review(self):
        for child in self.summary.get_children():
            self.summary.remove(child)
        for name, value in (("Destination", f'{self.disk["path"]}   ·   {self.disk["bytes"]/1024**3:.1f} GiB'),
                            ("System", self.desktop.get_active_text() + "   ·   " + self.init.get_active_text()),
                            ("Account", self.fields["username"].get_text() + "  @  " + self.fields["hostname"].get_text())):
            row = Gtk.Box(spacing=12)
            l = label(name, "details")
            l.set_size_request(98, -1)
            row.add(l)
            row.add(label(value, "fact-value"))
            self.summary.add(row)
        self.summary.show_all()
        self.erase.set_label("Erase all data on " + self.disk["path"])
        self.erase.set_active(False)

    def progress_page(self):
        box = column(13)
        self.result_title = label("Installing ZackOS", "heading")
        self.activity = label("", "lede", True)
        self.meter = Gtk.ProgressBar()
        box.add(self.result_title)
        box.add(self.activity)
        box.add(self.meter)
        self.detail_text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view = Gtk.ScrolledWindow()
        view.set_size_request(-1, 100)
        view.add(self.detail_text)
        self.details = Gtk.Expander(label="Installation details")
        self.details.add(view)
        box.add(self.details)
        return box

    def go(self, page):
        self.page = page
        self.notice.set_text("")
        self.stack.set_visible_child_name(str(page))
        self.scroll.get_vadjustment().set_value(0)
        for n, (row, number) in enumerate(self.rail_steps):
            ctx = row.get_style_context()
            for name in ("past", "current"):
                ctx.remove_class(name)
            if n < page:
                ctx.add_class("past")
            elif n == page:
                ctx.add_class("current")
            number.set_text("✓" if n < page else str(n+1))
        self.back.set_label("Try ZackOS" if page == 0 else "Back")
        self.forward.set_visible(True)
        self.forward.set_sensitive(True)
        self.forward.set_label(("Choose a disk  →", "Continue  →", "Review  →", "Install ZackOS", "Restart now")[page])
        if page == 1:
            self.load_disks()
        elif page == 3:
            self.update_review()
            self.forward.set_sensitive(False)

    def values(self):
        import re
        values = {key: field.get_text() for key, field in self.fields.items()}
        if self.desktop.get_active_id() == "i3" and values["username"] == "lightdm":
            raise ValueError("LightDM uses this account for the sign-in screen. Choose another username.")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", values["username"]) or values["username"] == "root":
            raise ValueError("Choose a regular username using lowercase letters, digits, underscores or dashes.")
        if (len(values["hostname"]) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
                                                     for p in values["hostname"].split("."))):
            raise ValueError("Enter a valid computer name.")
        for kind, name in (("user", "desktop"), ("root", "root")):
            if not values[kind+"_password"] or values[kind+"_password"] != values.pop(kind+"_confirm"):
                raise ValueError(f"Enter matching {name} passwords.")
        return values

    def next(self):
        try:
            if self.page == 4:
                if self.phase == "done":
                    self.auto_at = None
                    request("reboot")
                elif self.phase == "failed":
                    self.go(1)
                return
            if self.page == 1 and not self.disk:
                return
            if self.page in (2, 3):
                fields = self.values()
            if self.page == 3:
                if not self.erase.get_active():
                    return
                request("install", disk=self.disk["path"], identity=self.disk["identity"], erase=True,
                        desktop=self.desktop.get_active_id(), init=self.init.get_active_id(), **fields)
                self.started_here = True
                for key, field in self.fields.items():
                    if key.endswith(("password", "confirm")):
                        field.set_text("")
                self.go(4)
                self.poll()
            else:
                self.go(self.page+1)
        except (ValueError, OSError) as exc:
            self.notice.set_text(str(exc))

    def previous(self):
        if self.page in (0, 4):
            self.auto_at = None
            self.close()
        else:
            self.go(self.page-1)

    def poll(self):
        try:
            state = request("status")
        except (ValueError, OSError):
            if self.page == 4:
                self.notice.set_text("Waiting for the installation service…")
            return True
        phase = state["phase"]
        if phase == "ready" or (phase == "failed" and self.phase == "failed" and self.page != 4):
            return True
        if self.page != 4:
            self.go(4)
        if phase == "done" and self.phase != "done" and self.started_here and self.auto.get_active():
            self.auto_at = time.monotonic()+20
        self.phase = phase
        self.back.set_label("Stay in live session" if phase == "done" else "Return to desktop")
        self.forward.set_visible(phase != "working")
        self.result_title.set_text({"working": "Installing ZackOS", "done": "ZackOS is ready", "failed": "Installation stopped"}[phase])
        message = state["message"]
        if phase == "done" and self.started_here and self.desktop.get_active_id() == "i3":
            username = self.fields["username"].get_text()
            message = f"After restarting, sign in as {username} to open i3."
        self.activity.set_text(message)
        self.detail_text.get_buffer().set_text("\n".join(state.get("log", [])))
        self.meter.set_fraction(1.0 if phase == "done" else state["step"] / 5)
        if phase == "failed":
            self.forward.set_label("Review destination")
        if phase == "done":
            self.forward.set_label("Restart now")
            self.activity.set_text("Restart to boot your installed system.")
            if self.auto_at is not None:
                remaining = max(0, math.ceil(self.auto_at-time.monotonic()))
                self.activity.set_text(f"Restarting into your installed system in {remaining} seconds.")
                if not remaining:
                    self.next()
        return True


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.zackos.Installer", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            css = Gtk.CssProvider()
            css.load_from_path(str(HERE/"installer.css"))
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window = Wizard(self)
        window.present()


if __name__ == "__main__":
    sys.exit(Application().run(sys.argv))
