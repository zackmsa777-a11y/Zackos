"""The X11 dock, workspace switcher and live system meters."""
from collections import deque
from datetime import datetime
from pathlib import Path
import math

from gi.repository import Gdk, GdkX11, Gtk, Pango
from Xlib import Xatom

HEIGHT = 48
SPACES = ("desk", "web", "code", "files", "media")


def styled(widget, name):
    widget.get_style_context().add_class(name)
    return widget


def label(text, name):
    return styled(Gtk.Label(label=text), name)


def rounded(cr, x, y, width, height, radius):
    radius = min(radius, height / 2, width / 2)
    for cx, cy, start in ((x + width - radius, y + radius, -90),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, 90),
                          (x + radius, y + radius, 180)):
        cr.arc(cx, cy, radius, math.radians(start), math.radians(start + 90))
    cr.close_path()


class Glyph(Gtk.DrawingArea):
    """Small native glyphs keep the panel and bundled tools legible at any scale."""
    def __init__(self, name, size):
        super().__init__()
        self.name = name
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self.draw_icon)

    def draw_icon(self, area, cr):
        cr.scale(area.get_allocated_width()/24, area.get_allocated_height()/24)
        color = area.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        cr.set_source_rgb(color.red, color.green, color.blue)
        cr.set_line_width(1.8)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        if self.name == "grid":
            for x in (2, 13):
                for y in (2, 13):
                    rounded(cr, x, y, 8, 8, 2)
                    cr.fill()
        elif self.name == "folder":
            cr.move_to(2, 7)
            cr.line_to(2, 5)
            cr.line_to(9, 5)
            cr.line_to(11, 8)
            cr.line_to(21, 8)
            cr.line_to(21, 19)
            cr.line_to(2, 19)
            cr.close_path()
            cr.stroke()
            cr.move_to(3, 10)
            cr.line_to(20, 10)
            cr.stroke()
        elif self.name == "terminal":
            rounded(cr, 1.5, 4, 21, 16, 3)
            cr.stroke()
            cr.move_to(5.5, 9)
            cr.line_to(9, 12)
            cr.line_to(5.5, 15)
            cr.move_to(12, 15)
            cr.line_to(17, 15)
            cr.stroke()
        elif self.name == "edit":
            cr.move_to(4, 15)
            cr.line_to(15, 4)
            cr.line_to(19, 8)
            cr.line_to(8, 19)
            cr.line_to(3, 20)
            cr.close_path()
            cr.stroke()
            cr.move_to(12, 20)
            cr.line_to(21, 20)
            cr.stroke()
        elif self.name == "settings":
            for y, x in ((6, 8), (12, 15), (18, 10)):
                cr.move_to(3, y)
                cr.line_to(x-2, y)
                cr.move_to(x+2, y)
                cr.line_to(21, y)
                cr.stroke()
                cr.arc(x, y, 2, 0, 2*math.pi)
                cr.fill()


class Motion:
    """One interruptible frame-clock transition, respecting GTK's motion setting."""
    def __init__(self, widget):
        self.widget = widget
        self.tick = None

    def run(self, duration, frame, finished=None):
        if self.tick is not None:
            self.widget.remove_tick_callback(self.tick)
            self.tick = None
        if not self.widget.get_settings().get_property("gtk-enable-animations"):
            frame(1.0)
            if finished:
                finished()
            return
        start = self.widget.get_frame_clock().get_frame_time()

        def advance(widget, clock):
            t = min(1, (clock.get_frame_time() - start) / (duration * 1000))
            frame(1 - (1 - t) ** 3)
            if t < 1:
                return True
            self.tick = None
            if finished:
                finished()
            return False

        self.tick = self.widget.add_tick_callback(advance)


class Workspaces(Gtk.Overlay):
    def __init__(self, select, compact):
        super().__init__()
        self.select, self.compact = select, compact
        self.current = None
        self.pill = (0, 0)
        self.targets = {}
        self.buttons = {}
        self.canvas = Gtk.DrawingArea()
        self.canvas.connect("draw", self.paint)
        self.add(self.canvas)
        self.row = Gtk.Box()
        self.add_overlay(self.row)
        self.motion = Motion(self.canvas)
        self.set_valign(Gtk.Align.CENTER)

    def update(self, names, active, urgent):
        if list(self.targets) != list(names):
            for button in self.row.get_children():
                button.destroy()
            self.targets.clear()
            self.buttons.clear()
            x = 0
            for name in names:
                num, sep, title = name.partition(":")
                text = (num if self.compact else title.capitalize()) if sep else name
                button = styled(Gtk.Button(label=text), "workspace")
                button.set_can_focus(False)
                button.set_tooltip_text(name)
                button.get_accessible().set_name("Workspace " + name)
                button.connect("clicked", lambda _button, ws=name: self.select(ws))
                width = max(33 if self.compact else 47,
                            min(128, button.create_pango_layout(text).get_pixel_size()[0] + 21))
                button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
                button.set_size_request(width, 30)
                self.row.pack_start(button, False, False, 0)
                self.buttons[name] = button
                self.targets[name] = (x, width)
                x += width
            self.canvas.set_size_request(x, 30)
            self.row.show_all()
            self.current = None
        for name, button in self.buttons.items():
            ctx = button.get_style_context()
            (ctx.add_class if name == active else ctx.remove_class)("active")
            (ctx.add_class if name in urgent else ctx.remove_class)("urgent")
        if active == self.current:
            return
        start = self.pill
        target = self.targets.get(active, (0, 0))
        old, self.current = self.current, active

        def move(fraction):
            self.pill = tuple(a + (b-a)*fraction for a, b in zip(start, target))
            self.canvas.queue_draw()

        if old is None or not self.canvas.get_mapped():
            move(1)
        else:
            self.motion.run(230, move)

    def paint(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        rounded(cr, 0, 0, w, h, 10)
        cr.set_source_rgb(.105, .151, .164)
        cr.fill()
        x, width = self.pill
        if width > 5:
            rounded(cr, x+2, 2, width-4, h-4, 8)
            cr.set_source_rgb(.208, .284, .253)
            cr.fill_preserve()
            cr.set_source_rgba(.57, .78, .63, .24)
            cr.set_line_width(1)
            cr.stroke()


class LoadChart(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_size_request(44, 20)
        self.set_valign(Gtk.Align.CENTER)
        self.history = deque([0.0]*16, maxlen=16)
        self.connect("draw", self.paint)

    def paint(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        values = list(self.history)
        for i, value in enumerate(values):
            x = 1 + (w-2)*i/(len(values)-1)
            y = h - 3 - (h-6)*min(1, max(0, value))
            (cr.move_to if i == 0 else cr.line_to)(x, y)
        cr.set_source_rgb(.48, .83, .69)
        cr.set_line_width(1.6)
        cr.set_line_join(1)
        cr.stroke_preserve()
        cr.line_to(w-1, h-1)
        cr.line_to(1, h-1)
        cr.close_path()
        cr.set_source_rgba(.48, .83, .69, .13)
        cr.fill()


class SystemStats:
    def __init__(self):
        self.previous = None

    def read(self):
        with Path("/proc/stat").open() as stat:
            ticks = [int(x) for x in stat.readline().split()[1:9]]
        total, idle = sum(ticks), sum(ticks[3:5])
        load = 0
        if self.previous:
            delta = total - self.previous[0]
            if delta:
                load = 1 - (idle-self.previous[1])/delta
        self.previous = (total, idle)
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemTotal", "MemAvailable"):
                info[key] = int(value.split()[0])
        return load, (info["MemTotal"]-info["MemAvailable"])/1048576


class Panel(Gtk.Window):
    def __init__(self, controller, output, scale):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        rect = output.rect
        self.output = output.name
        self.bounds = (rect.x//scale, rect.y//scale, rect.width//scale, rect.height//scale)
        x, y, width, _ = self.bounds
        self.set_title("ZackOS Panel")
        self.set_wmclass("zackos-panel", "ZackOSPanel")
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_accept_focus(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_size_request(width, HEIGHT)
        self.move(x, y)
        self.stick()

        def set_strut(window):
            display = controller.x11
            xid = GdkX11.X11Window.get_xid(window.get_window())
            dock = display.create_resource_object("window", xid)
            top = rect.y + HEIGHT*scale
            dock.change_property(display.intern_atom("_NET_WM_STRUT"), Xatom.CARDINAL, 32,
                                 [0, 0, top, 0])
            dock.change_property(display.intern_atom("_NET_WM_STRUT_PARTIAL"), Xatom.CARDINAL, 32,
                                 [0, 0, top, 0, 0, 0, 0, 0, rect.x, rect.x + rect.width - 1, 0, 0])
            display.sync()

        self.connect("realize", set_strut)
        bar = styled(Gtk.Box(spacing=12 if width >= 800 else 5), "panel")
        self.add(bar)
        self.apps = styled(Gtk.Button(), "apps")
        self.apps.set_valign(Gtk.Align.CENTER)
        self.apps.set_can_focus(False)
        self.apps.set_tooltip_text("Applications (Super+D)")
        self.apps.get_accessible().set_name("Open applications")
        contents = Gtk.Box(spacing=7)
        contents.add(Glyph("grid", 15))
        if width >= 500:
            contents.add(Gtk.Label(label="Apps"))
        self.apps.add(contents)
        self.apps.connect("clicked", lambda _btn: controller.toggle_launcher(self))
        bar.pack_start(self.apps, False, False, 0)
        self.spaces = Workspaces(controller.switch_workspace, width < 740)
        bar.pack_start(self.spaces, False, False, 0)
        self.mode = label("", "mode")
        self.mode.set_no_show_all(True)
        self.mode.set_ellipsize(Pango.EllipsizeMode.END)
        self.mode.set_max_width_chars(25)
        bar.pack_start(self.mode, False, False, 0)

        self.focus_title = label("", "focus-title")
        self.focus_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.focus_title.set_single_line_mode(True)
        self.focus_title.set_max_width_chars(32)
        bar.pack_start(self.focus_title, True, True, 0)
        self.cpu, self.mem = label("0%", "meter-value"), label("", "meter-value")
        self.cpu.set_width_chars(4)
        self.cpu.set_xalign(1)
        self.chart = LoadChart()
        if width >= 720:
            meters = styled(Gtk.Box(spacing=8), "metrics")
            meters.set_valign(Gtk.Align.CENTER)
            meters.pack_start(label("CPU", "meter-label"), False, False, 0)
            meters.pack_start(self.chart, False, False, 0)
            meters.pack_start(self.cpu, False, False, 0)
            if width >= 865:
                meters.add(styled(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), "rule"))
                meters.add(label("MEM", "meter-label"))
                meters.add(self.mem)
            bar.pack_start(meters, False, False, 0)
        self.date, self.clock = label("", "date"), label("", "clock")
        if width >= 1020:
            bar.pack_start(self.date, False, False, 0)
        bar.pack_start(self.clock, False, False, 0)

    def update_stats(self, load, memory):
        self.chart.history.append(load)
        self.chart.queue_draw()
        self.cpu.set_text(f"{load:.0%}")
        self.mem.set_text(f"{memory:.1f} GiB")
        now = datetime.now()
        self.clock.set_text(now.strftime("%H:%M"))
        self.clock.set_tooltip_text(now.strftime("%A, %d %B %Y"))
        self.date.set_text(now.strftime("%a, %b %d"))
