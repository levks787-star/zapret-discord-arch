#!/usr/bin/env python3
"""Zapret Discord (zapret-discord-arch) — GTK4/libadwaita GUI for zapret on Arch Linux.

Обёртка над адаптером Sergeydigl3/zapret-discord-youtube-linux:
systemd-сервис zapret_discord_youtube, conf.env, ./service.sh strategy list / config set.
Привилегированные действия выполняет zapret-discord-helper через pkexec (polkit).
Флаги nfqws приложение не формирует — только вызывает существующие скрипты.
"""
import os
import re
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = "io.github.zapret_discord_gui"
APP_NAME = "Zapret Discord"
VERSION = "1.0.0"
REPO_URL = "https://github.com/levks787-star/zapret-discord-arch"

ADAPTER_DIR = os.environ.get("ZDG_ADAPTER_DIR", "/opt/zapret-discord-youtube-linux")
SERVICE = "zapret_discord_youtube.service"
UNIT_FILE = os.environ.get("ZDG_UNIT_FILE", "/etc/systemd/system/" + SERVICE)
Z2_SERVICE = "zapret2.service"
FW_BACKENDS = ["auto", "nftables", "iptables"]
DEFAULT_STRATEGY = "general.bat"
POLL_SECONDS = 3

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def find_helper():
    env = os.environ.get("ZDG_HELPER")
    if env:
        return env
    for p in ("/usr/lib/zapret-discord-arch/zapret-discord-helper",
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "zapret-discord-helper")):
        if os.path.exists(p):
            return p
    return "/usr/lib/zapret-discord-arch/zapret-discord-helper"


HELPER = find_helper()
# ZDG_PKEXEC позволяет подменить pkexec в тестах (например "env ZDG_TEST=1 ZDG_DRY=1").
PKEXEC = os.environ.get("ZDG_PKEXEC", "pkexec").split()


def helper_argv(*args):
    return PKEXEC + [HELPER] + list(args)


def sh(argv, timeout=8):
    """Run a command, return (rc, stdout). Never raises."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def read_conf():
    conf = {}
    try:
        with open(os.path.join(ADAPTER_DIR, "conf.env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return conf


def list_strategies():
    """Strategies exactly as the adapter reports them (service.sh strategy list)."""
    names = []
    svc = os.path.join(ADAPTER_DIR, "service.sh")
    if os.access(svc, os.X_OK):
        rc, out = sh([svc, "strategy", "list"], timeout=15)
        if rc == 0:
            names = [l.strip() for l in out.splitlines() if l.strip().endswith(".bat")]
    if not names:  # fallback: те же каталоги, что в get_strategies() адаптера
        found = set()
        for d, pats in ((os.path.join(ADAPTER_DIR, "custom-strategies"), ("",)),
                        (os.path.join(ADAPTER_DIR, "zapret-latest"), ("general", "discord"))):
            try:
                for f in os.listdir(d):
                    if f.endswith(".bat") and f.startswith(pats) and os.path.isfile(os.path.join(d, f)):
                        found.add(f)
            except OSError:
                pass
        names = sorted(found)
    return [n for n in names if SAFE_NAME_RE.match(n)]


def list_interfaces():
    names = []
    rc, out = sh(["ip", "-o", "link", "show"])
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 2:
                names.append(parts[1].strip().split("@")[0])
    if not names:
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            names = []
    return ["any"] + [n for n in names if n != "lo"]


def collect_status():
    st = {}
    st["adapter"] = os.access(os.path.join(ADAPTER_DIR, "service.sh"), os.X_OK)
    st["unit"] = os.path.exists(UNIT_FILE)
    _, st["active"] = sh(["systemctl", "is-active", SERVICE])
    _, st["enabled"] = sh(["systemctl", "is-enabled", SERVICE])
    _, z2a = sh(["systemctl", "is-active", Z2_SERVICE])
    _, z2e = sh(["systemctl", "is-enabled", Z2_SERVICE])
    st["z2"] = z2a == "active" or z2e in ("enabled", "enabled-runtime")
    st["z2_state"] = "%s / %s" % (z2a or "?", z2e or "?")
    rc, out = sh(["pgrep", "-a", "-x", "nfqws"])
    st["nfqws"] = out.splitlines() if rc == 0 else []
    rc, out = sh(["pgrep", "-x", "nfqws2"])
    st["nfqws2"] = rc == 0
    rc, out = sh(["nft", "list", "table", "inet", "zapretunix"])
    st["nft"] = out.count("queue") if rc == 0 else None
    st["conf"] = read_conf()
    return st


def strip_ansi(s):
    return ANSI_RE.sub("", s)


class OutputDialog(Adw.Dialog):
    """Окно с потоковым выводом команды (установка, проверка, журнал)."""

    def __init__(self, title, argv, on_done=None, extra_button=None):
        super().__init__(title=title, content_width=760, content_height=520)
        self._on_done = on_done
        self._proc = None
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.spinner = Gtk.Spinner(spinning=True)
        header.pack_start(self.spinner)
        if extra_button:
            label, callback = extra_button
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda *_: callback(self))
            header.pack_end(b)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Скопировать вывод")
        copy.connect("clicked", self._copy)
        header.pack_end(copy)
        view.add_top_bar(header)

        self.buf = Gtk.TextBuffer()
        tt = self.buf.get_tag_table()
        for name, props in (("ok", {"foreground": "#26a269"}), ("bad", {"foreground": "#e01b24"}),
                            ("head", {"weight": 700})):
            tag = Gtk.TextTag(name=name)
            for k, v in props.items():
                tag.set_property(k, v)
            tt.add(tag)
        self.text = Gtk.TextView(buffer=self.buf, editable=False, monospace=True, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12, bottom_margin=12,
                                 left_margin=12, right_margin=12)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, child=self.text)
        view.set_content(self.scroll)
        self.status = Gtk.Label(xalign=0, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status.add_css_class("dim-label")
        view.add_bottom_bar(self.status)
        self.set_child(view)
        self.connect("closed", self._closed)
        self.run(argv)

    def run(self, argv):
        self.buf.set_text("")
        self.append("$ " + " ".join(argv) + "\n", "head")
        self.status.set_text("Выполняется…")
        self.spinner.set_spinning(True)
        try:
            self._proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.append("Не удалось запустить: %s\n" % e.message, "bad")
            self._finish(127)
            return
        self._stream = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        self._stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, self._proc)

    def append(self, line, tag=None):
        line = strip_ansi(line)
        if tag is None:
            if "[OK]" in line:
                tag = "ok"
            elif "[!!]" in line or "ОШИБКА" in line or "ERROR" in line:
                tag = "bad"
            elif line.startswith("==>") or re.match(r"^\d+\. ", line):
                tag = "head"
        end = self.buf.get_end_iter()
        if tag:
            self.buf.insert_with_tags_by_name(end, line, tag)
        else:
            self.buf.insert(end, line)
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def _on_line(self, stream, res, proc):
        if proc is not self._proc:
            return
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            proc.wait_async(None, self._on_exit, proc)
            return
        self.append(line + "\n")
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, proc)

    def _on_exit(self, proc, res, _p):
        try:
            proc.wait_finish(res)
            rc = proc.get_exit_status()
        except GLib.Error:
            rc = 1
        if proc is self._proc:
            self._finish(rc)

    def _finish(self, rc):
        self.spinner.set_spinning(False)
        if rc == 0:
            self.status.set_text("Готово (код 0)")
        elif rc in (126, 127) and PKEXEC and PKEXEC[0] == "pkexec":
            self.status.set_text("Отменено или нет прав (pkexec, код %d)" % rc)
        else:
            self.status.set_text("Завершено с ошибкой (код %d)" % rc)
        self._proc = None
        if self._on_done:
            self._on_done(rc)

    def _copy(self, *_):
        s, e = self.buf.get_bounds()
        self.get_clipboard().set(self.buf.get_text(s, e, False))

    def _closed(self, *_):
        if self._proc is not None:
            # процесс под pkexec принадлежит root — не убиваем, только перестаём читать
            self._proc = None


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=580, default_height=860)
        self._updating = False
        self._busy = False
        self._polling = False
        self._dirty = False
        self._status = {}
        self._strategies = []
        self._ifaces = []

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=APP_NAME, subtitle="обход DPI для Discord"))
        self.busy_spinner = Gtk.Spinner()
        header.pack_start(self.busy_spinner)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Обновить статус и списки")
        refresh.connect("clicked", lambda *_: self.reload_lists())
        header.pack_end(refresh)
        menu = Gio.Menu()
        menu.append("Переустановить адаптер…", "win.reinstall")
        menu.append("Удалить сервис systemd…", "win.remove-service")
        menu.append("О программе", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="Обнаружен zapret2.service (вариант B). Два варианта одновременно "
                                       "работать не могут — управление отключено.",
                                 button_label="Отключить zapret2")
        self.banner.connect("button-clicked", self.on_disable_zapret2)
        view.add_top_bar(self.banner)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24,
                       margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=640, child=page)
        view.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # --- Главный переключатель -------------------------------------------------------
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        self.hero_icon = Gtk.Image(icon_name="network-offline-symbolic", pixel_size=72)
        self.hero_title = Gtk.Label(label="Обход выключен")
        self.hero_title.add_css_class("title-1")
        self.hero_status = Gtk.Label(label="…", wrap=True, justify=Gtk.Justification.CENTER)
        self.main_switch = Gtk.Switch(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                      tooltip_text="Включить / выключить сервис zapret")
        self.main_switch.add_css_class("zdg-big-switch")
        self.main_switch.connect("state-set", self.on_main_switch)
        for w in (self.hero_icon, self.hero_title, self.hero_status, self.main_switch):
            hero.append(w)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=4)
        self.restart_btn = Gtk.Button(label="Перезапустить")
        self.restart_btn.add_css_class("pill")
        self.restart_btn.connect("clicked", lambda *_: self.helper_quick(["restart"], "Сервис перезапущен"))
        btns.append(self.restart_btn)
        hero.append(btns)
        page.append(hero)

        # --- Установка (если чего-то нет) -----------------------------------------------
        self.install_group = Adw.PreferencesGroup(title="Установка")
        self.install_row = Adw.ActionRow(title="Адаптер zapret не установлен",
                                         subtitle="Скачает адаптер в /opt/zapret-discord-youtube-linux, nfqws "
                                                  "(bol-van/zapret v72.9), стратегии Flowseal и создаст сервис")
        b = Gtk.Button(label="Установить", valign=Gtk.Align.CENTER)
        b.add_css_class("suggested-action")
        b.connect("clicked", self.on_install_adapter)
        self.install_row.add_suffix(b)
        self.install_group.add(self.install_row)
        self.unit_row = Adw.ActionRow(title="Сервис systemd не установлен",
                                      subtitle="service.sh service install — с текущими настройками conf.env")
        b = Gtk.Button(label="Установить сервис", valign=Gtk.Align.CENTER)
        b.add_css_class("suggested-action")
        b.connect("clicked", lambda *_: self.helper_dialog(["service-install"], "Установка сервиса"))
        self.unit_row.add_suffix(b)
        self.install_group.add(self.unit_row)
        self.install_group.set_visible(False)  # покажем после первого опроса статуса
        page.append(self.install_group)

        # --- Состояние ------------------------------------------------------------------
        g = Adw.PreferencesGroup(title="Состояние")
        self.row_service = Adw.ActionRow(title="Сервис", subtitle=SERVICE)
        self.lbl_service = self._status_label()
        self.row_service.add_suffix(self.lbl_service)
        self.row_nfqws = Adw.ActionRow(title="Процесс nfqws", subtitle_lines=2)
        self.lbl_nfqws = self._status_label()
        self.row_nfqws.add_suffix(self.lbl_nfqws)
        self.row_nft = Adw.ActionRow(title="nftables", subtitle="таблица inet zapretunix")
        self.lbl_nft = self._status_label()
        self.row_nft.add_suffix(self.lbl_nft)
        b = Gtk.Button(icon_name="system-search-symbolic", valign=Gtk.Align.CENTER,
                       tooltip_text="Показать правила (нужен root)")
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: self.helper_dialog(["nft-status"], "nftables: inet zapretunix"))
        self.row_nft.add_suffix(b)
        self.row_current = Adw.ActionRow(title="Активная конфигурация", subtitle="conf.env")
        for r in (self.row_service, self.row_nfqws, self.row_nft, self.row_current):
            g.add(r)
        page.append(g)

        # --- Стратегия и сеть -----------------------------------------------------------
        self.settings_group = Adw.PreferencesGroup(
            title="Стратегия и сеть",
            description="Если Discord не грузится или голос висит на «RTC connecting» — "
                        "попробуйте другую стратегию (general_alt*, fake_tls_auto…).")
        self.strategy_model = Gtk.StringList()
        self.row_strategy = Adw.ComboRow(title="Стратегия", model=self.strategy_model)
        self.row_strategy.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.row_strategy.set_enable_search(True)
        prev_b = Gtk.Button(icon_name="go-previous-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Предыдущая стратегия")
        next_b = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Следующая стратегия")
        for bb, d in ((prev_b, -1), (next_b, 1)):
            bb.add_css_class("flat")
            bb.connect("clicked", self.on_step_strategy, d)
        self.row_strategy.add_prefix(prev_b)
        self.row_strategy.add_suffix(next_b)
        self.iface_model = Gtk.StringList()
        self.row_iface = Adw.ComboRow(title="Сетевой интерфейс", subtitle="any — весь трафик",
                                      model=self.iface_model)
        self.row_fw = Adw.ComboRow(title="Бэкенд файрвола", model=Gtk.StringList.new(FW_BACKENDS))
        self.row_gt = Adw.SwitchRow(title="GameFilter TCP", subtitle="порты 1024-65535 (игры)")
        self.row_gu = Adw.SwitchRow(title="GameFilter UDP", subtitle="порты 1024-65535 (игры)")
        for r in (self.row_strategy, self.row_iface, self.row_fw, self.row_gt, self.row_gu):
            self.settings_group.add(r)
        for r in (self.row_strategy, self.row_iface, self.row_fw):
            r.connect("notify::selected", self.on_setting_changed)
        for r in (self.row_gt, self.row_gu):
            r.connect("notify::active", self.on_setting_changed)
        page.append(self.settings_group)

        abox = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.revert_btn = Gtk.Button(label="Сбросить")
        self.revert_btn.add_css_class("pill")
        self.revert_btn.connect("clicked", lambda *_: self.load_conf_into_ui(force=True))
        self.apply_btn = Gtk.Button(label="Применить")
        self.apply_btn.add_css_class("pill")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self.on_apply)
        abox.append(self.revert_btn)
        abox.append(self.apply_btn)
        self.apply_box = abox
        page.append(abox)

        # --- Дополнительно --------------------------------------------------------------
        self.extra_group = Adw.PreferencesGroup(title="Дополнительно")
        self.row_autostart = Adw.SwitchRow(title="Автозапуск при загрузке",
                                           subtitle="systemctl enable " + SERVICE)
        self.row_autostart.connect("notify::active", self.on_autostart)
        self.extra_group.add(self.row_autostart)
        self.extra_group.add(self._button_row("Журнал сервиса", "journalctl -u zapret_discord_youtube -b",
                                              "Открыть", self.on_logs))
        self.extra_group.add(self._button_row("Проверить Discord", "сервис, nfqws, nftables, HTTPS до discord.com",
                                              "Проверить", lambda *_: self.helper_dialog(["check"], "Проверка Discord")))
        self.extra_group.add(self._button_row("Переустановить зависимости",
                                              "pacman (git curl nftables…) + service.sh download-deps --default",
                                              "Обновить", self.on_update_deps))
        page.append(self.extra_group)

        self._install_css()
        self.set_actions()
        self.reload_lists()
        GLib.timeout_add_seconds(POLL_SECONDS, self._poll_tick)

    # ---------------------------------------------------------------- helpers / widgets
    def _status_label(self):
        lbl = Gtk.Label(valign=Gtk.Align.CENTER)
        lbl.add_css_class("caption-heading")
        return lbl

    def _button_row(self, title, subtitle, label, cb):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        b.connect("clicked", cb)
        row.add_suffix(b)
        return row

    def _install_css(self):
        css = Gtk.CssProvider()
        data = (".zdg-big-switch { min-width: 96px; min-height: 44px; border-radius: 22px; }"
                ".zdg-big-switch slider { min-width: 40px; min-height: 40px; border-radius: 20px; }")
        try:
            css.load_from_string(data)
        except AttributeError:  # GTK < 4.12
            css.load_from_data(data.encode())
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def set_actions(self):
        for name, cb in (("about", self.on_about), ("reinstall", self.on_install_adapter),
                         ("remove-service", self.on_remove_service)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    @staticmethod
    def _set_class(lbl, ok):
        for c in ("success", "error", "warning", "dim-label"):
            lbl.remove_css_class(c)
        lbl.add_css_class({True: "success", False: "error", None: "dim-label"}[ok])

    def _selected(self, row, model):
        i = row.get_selected()
        if i == Gtk.INVALID_LIST_POSITION or i >= model.get_n_items():
            return None
        return model.get_string(i)

    @staticmethod
    def _fill(model, items):
        model.splice(0, model.get_n_items(), items)

    # ---------------------------------------------------------------- status refresh
    def reload_lists(self):
        def work():
            s = list_strategies()
            i = list_interfaces()
            st = collect_status()
            GLib.idle_add(self._lists_loaded, s, i, st)
        threading.Thread(target=work, daemon=True).start()

    def _lists_loaded(self, strategies, ifaces, st):
        self._updating = True
        conf = st.get("conf", {})
        cur = conf.get("strategy")
        if cur and cur not in strategies and SAFE_NAME_RE.match(cur):
            strategies = [cur] + strategies
        if not strategies:
            strategies = [DEFAULT_STRATEGY]
        cur_if = conf.get("interface")
        if cur_if and cur_if not in ifaces:
            ifaces = ifaces + [cur_if]
        self._strategies, self._ifaces = strategies, ifaces
        self._fill(self.strategy_model, strategies)
        self._fill(self.iface_model, ifaces)
        self._updating = False
        self._apply_status(st)
        self.load_conf_into_ui(force=True)
        return False

    def _poll_tick(self):
        if not self._polling and not self._busy:
            self._polling = True

            def work():
                st = collect_status()
                GLib.idle_add(self._polled, st)
            threading.Thread(target=work, daemon=True).start()
        return True

    def _polled(self, st):
        self._polling = False
        if not self._busy:
            self._apply_status(st)
            if not self._dirty:
                self.load_conf_into_ui()
        return False

    def load_conf_into_ui(self, force=False):
        conf = self._status.get("conf", {})
        self._updating = True
        try:
            s = conf.get("strategy", DEFAULT_STRATEGY)
            if s in self._strategies:
                self.row_strategy.set_selected(self._strategies.index(s))
            i = conf.get("interface", "any")
            if i in self._ifaces:
                self.row_iface.set_selected(self._ifaces.index(i))
            fb = conf.get("firewall_backend", "auto")
            self.row_fw.set_selected(FW_BACKENDS.index(fb) if fb in FW_BACKENDS else 0)
            self.row_gt.set_active(conf.get("gamefiltertcp") == "true")
            self.row_gu.set_active(conf.get("gamefilterudp") == "true")
        finally:
            self._updating = False
        if force:
            self._dirty = False
        self._update_apply_state()

    def _apply_status(self, st):
        self._status = st
        self._updating = True
        try:
            active = st.get("active") == "active"
            adapter, unit, z2 = st.get("adapter"), st.get("unit"), st.get("z2")
            nfq = st.get("nfqws", [])

            self.banner.set_revealed(bool(z2))
            self.install_group.set_visible(not adapter or not unit)
            self.install_row.set_visible(not adapter)
            self.unit_row.set_visible(bool(adapter) and not unit)

            self.main_switch.set_active(active)
            self.main_switch.set_state(active)
            can_control = bool(adapter and unit and not z2) and not self._busy
            self.main_switch.set_sensitive(can_control or (active and not self._busy))
            self.restart_btn.set_sensitive(can_control)
            settings_ok = bool(adapter and not z2) and not self._busy
            self.settings_group.set_sensitive(settings_ok)
            self.apply_box.set_sensitive(settings_ok)
            self.row_autostart.set_sensitive(bool(unit and not z2) and not self._busy)
            self.row_autostart.set_active(st.get("enabled") in ("enabled", "enabled-runtime"))

            if active and nfq:
                self.hero_title.set_label("Обход включён")
                self.hero_icon.set_from_icon_name("security-high-symbolic")
                self.hero_status.set_label("Discord должен работать. Если нет — смените стратегию ниже.")
                self._set_class(self.hero_status, True)
            elif active:
                self.hero_title.set_label("Сервис запущен")
                self.hero_icon.set_from_icon_name("dialog-warning-symbolic")
                self.hero_status.set_label("…но процесс nfqws не найден. Откройте журнал.")
                self._set_class(self.hero_status, False)
            else:
                self.hero_title.set_label("Обход выключен")
                self.hero_icon.set_from_icon_name("network-offline-symbolic")
                if not adapter:
                    msg = "Адаптер не установлен — нажмите «Установить»."
                elif not unit:
                    msg = "Сервис не установлен — нажмите «Установить сервис»."
                elif z2:
                    msg = "Работает вариант B (zapret2): %s" % st.get("z2_state")
                else:
                    msg = "Сервис %s: %s" % (SERVICE, st.get("active") or "неизвестно")
                self.hero_status.set_label(msg)
                self._set_class(self.hero_status, False)

            self.lbl_service.set_label(st.get("active") or "неизвестно")
            self._set_class(self.lbl_service, active)
            if not unit:
                self.row_service.set_subtitle(SERVICE + " — не установлен")
            else:
                self.row_service.set_subtitle("%s — автозапуск: %s" % (SERVICE, st.get("enabled") or "?"))
            if nfq:
                self.lbl_nfqws.set_label("PID " + ", ".join(l.split()[0] for l in nfq))
                self.row_nfqws.set_subtitle("%d процесс(ов): %s" % (len(nfq), nfq[0][:120]))
                self._set_class(self.lbl_nfqws, True)
            else:
                self.lbl_nfqws.set_label("не запущен")
                self.row_nfqws.set_subtitle("pgrep -x nfqws")
                self._set_class(self.lbl_nfqws, False)
            if st.get("nfqws2"):
                self.row_nfqws.set_subtitle(self.row_nfqws.get_subtitle() + " · ВНИМАНИЕ: запущен и nfqws2")
            nft = st.get("nft")
            if nft is None:
                self.lbl_nft.set_label("нужен root" if active else "—")
                self.row_nft.set_subtitle("таблица inet zapretunix · детали по кнопке 🔍")
                self._set_class(self.lbl_nft, None)
            else:
                self.lbl_nft.set_label("%d правил queue" % nft)
                self.row_nft.set_subtitle("таблица inet zapretunix есть")
                self._set_class(self.lbl_nft, nft > 0)
            conf = st.get("conf", {})
            if conf:
                self.row_current.set_subtitle("%s · %s · fw=%s · gameTCP=%s · gameUDP=%s" % (
                    conf.get("strategy", "?"), conf.get("interface", "?"), conf.get("firewall_backend", "auto"),
                    conf.get("gamefiltertcp", "false"), conf.get("gamefilterudp", "false")))
            else:
                self.row_current.set_subtitle("conf.env отсутствует")
        finally:
            self._updating = False

    def _update_apply_state(self):
        self.apply_btn.set_label("Применить и перезапустить" if self._status.get("active") == "active"
                                 else "Применить")
        self.revert_btn.set_sensitive(self._dirty)
        self.row_current.set_title("Активная конфигурация" + (" · есть несохранённые изменения" if self._dirty else ""))

    def set_busy(self, busy):
        self._busy = busy
        self.busy_spinner.set_spinning(busy)
        if self._status:
            self._apply_status(self._status)

    # ---------------------------------------------------------------- running the helper
    def helper_quick(self, args, ok_text, on_ok=None):
        """Короткое действие: без окна; при ошибке показываем вывод."""
        if self._busy:
            return
        self.set_busy(True)
        try:
            proc = Gio.Subprocess.new(helper_argv(*args),
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.set_busy(False)
            self.error_dialog("Не удалось запустить pkexec", e.message)
            return

        def done(p, res):
            try:
                _, out, _ = p.communicate_utf8_finish(res)
                rc = p.get_exit_status()
            except GLib.Error as e:
                out, rc = e.message, 1
            self.set_busy(False)
            if rc == 0:
                self.toast(ok_text)
                if on_ok:
                    on_ok()
            elif rc in (126, 127) and PKEXEC[0] == "pkexec" and not (out or "").strip():
                self.toast("Действие отменено")
            else:
                self.error_dialog("Ошибка (код %d)" % rc, strip_ansi(out or "")[-3000:])
            self.reload_lists()
        proc.communicate_utf8_async(None, None, done)

    def helper_dialog(self, args, title, on_done=None):
        self.set_busy(True)

        def finished(rc):
            self.set_busy(False)
            self.reload_lists()
            if on_done:
                on_done(rc)
        OutputDialog(title, helper_argv(*args), on_done=finished).present(self)

    def error_dialog(self, heading, body):
        d = Adw.AlertDialog(heading=heading, body=body or "(нет вывода)")
        d.add_response("ok", "OK")
        d.present(self)

    def confirm(self, heading, body, label, cb, destructive=False):
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", "Отмена")
        d.add_response("go", label)
        d.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                  else Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("go")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: cb() if r == "go" else None)
        d.present(self)

    # ---------------------------------------------------------------- handlers
    def on_main_switch(self, sw, state):
        if self._updating:
            return False
        if state:
            self.helper_quick(["start"], "Обход включён")
        else:
            self.helper_quick(["stop"], "Обход выключен")
        return True  # реальное состояние выставит обновление статуса

    def on_setting_changed(self, *_):
        if self._updating:
            return
        self._dirty = True
        self._update_apply_state()

    def on_step_strategy(self, _b, delta):
        n = self.strategy_model.get_n_items()
        if n:
            i = self.row_strategy.get_selected()
            i = 0 if i == Gtk.INVALID_LIST_POSITION else (i + delta) % n
            self.row_strategy.set_selected(i)

    def on_apply(self, *_):
        s = self._selected(self.row_strategy, self.strategy_model)
        i = self._selected(self.row_iface, self.iface_model) or "any"
        fb = FW_BACKENDS[self.row_fw.get_selected()] if self.row_fw.get_selected() < 3 else "auto"
        if not s:
            self.toast("Нет стратегий — переустановите зависимости")
            return
        args = ["apply", s, i, str(self.row_gt.get_active()).lower(), str(self.row_gu.get_active()).lower(), fb]

        def ok():
            self._dirty = False
        self.helper_quick(args, "Применено: %s" % s, on_ok=ok)

    def on_autostart(self, row, _p):
        if self._updating:
            return
        self.helper_quick(["enable" if row.get_active() else "disable"],
                          "Автозапуск включён" if row.get_active() else "Автозапуск выключен")

    def on_logs(self, *_):
        argv = ["journalctl", "-u", SERVICE, "-b", "-n", "300", "--no-pager", "-o", "short-iso"]
        OutputDialog("Журнал " + SERVICE, argv,
                     extra_button=("С правами root", lambda dlg: dlg.run(helper_argv("logs", "300")))).present(self)

    def on_update_deps(self, *_):
        self.confirm("Переустановить зависимости?",
                     "pacman -S --needed git curl nftables grep sed procps-ng, затем повторно скачать nfqws "
                     "и стратегии Flowseal рекомендованных версий. Работающий сервис будет перезапущен.",
                     "Обновить", lambda: self.helper_dialog(["update-deps"], "Переустановка зависимостей"))

    def on_install_adapter(self, *_):
        if self._status.get("z2"):
            self.error_dialog("Сначала отключите zapret2", "Вариант A и вариант B нельзя ставить одновременно.")
            return
        s = self._selected(self.row_strategy, self.strategy_model) or DEFAULT_STRATEGY
        i = self._selected(self.row_iface, self.iface_model) or "any"
        self.confirm("Установить адаптер zapret?",
                     "Будет выполнен scripts/zapret-install.sh (вариант A): пакеты git curl nftables из репозиториев Arch, "
                     "адаптер Sergeydigl3 в /opt/zapret-discord-youtube-linux, nfqws v72.9 и стратегии Flowseal, "
                     "сервис %s.\n\nСтратегия: %s, интерфейс: %s" % (SERVICE, s, i),
                     "Установить", lambda: self.helper_dialog(["install-adapter", s, i], "Установка zapret"))

    def on_remove_service(self, *_):
        self.confirm("Удалить сервис?", "service.sh service remove: сервис будет остановлен и удалён из systemd. "
                     "Адаптер и conf.env останутся.", "Удалить",
                     lambda: self.helper_quick(["service-remove"], "Сервис удалён"), destructive=True)

    def on_disable_zapret2(self, *_):
        self.confirm("Отключить zapret2?", "systemctl disable --now zapret2.service — вариант B будет остановлен "
                     "и не будет запускаться при загрузке (пакет не удаляется).", "Отключить",
                     lambda: self.helper_quick(["disable-zapret2"], "zapret2 отключён"), destructive=True)

    def on_about(self, *_):
        kw = dict(application_name=APP_NAME, application_icon="zapret-discord-gui", version=VERSION,
                  developer_name="zapret-discord-arch",
                  comments="Графическое управление zapret на Arch Linux.\nАдаптер: Sergeydigl3/zapret-discord-"
                           "youtube-linux · nfqws: bol-van/zapret · стратегии: Flowseal",
                  website=REPO_URL,
                  license_type=Gtk.License.MIT_X11)
        if hasattr(Adw, "AboutDialog"):
            Adw.AboutDialog(**kw).present(self)
        else:
            Adw.AboutWindow(transient_for=self, **kw).present()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.get_active_window() or MainWindow(self)
        win.present()


def main():
    if "--version" in sys.argv:
        print(APP_NAME, VERSION)
        return 0
    if shutil.which(PKEXEC[0]) is None:
        print("предупреждение: %s не найден (пакет polkit)" % PKEXEC[0], file=sys.stderr)
    return App().run([a for a in sys.argv if a != "--version"])


if __name__ == "__main__":
    sys.exit(main())
