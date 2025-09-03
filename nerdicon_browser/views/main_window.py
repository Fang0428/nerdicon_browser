import os
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gtk, Gdk, Pango, Gio, GLib

from nerdicon_browser.models import IconItem


class IconBrowserWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Nerd Font Browser")
        self.set_default_size(900, 640)

        # Mapping from codepoint to glyph name for current font
        self.name_by_cp = {}
        self.current_font: Optional[str] = None
        self.search_text = ""
        self.family_list: list[str] = []

        # CSS for glyph font + tile styling
        self._load_css()

        # Toast overlay
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main box
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toast_overlay.set_child(vbox)

        # Header bar with search and font dropdown
        header = Adw.HeaderBar()
        vbox.append(header)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search name or codepoint (e.g. magnify or f135)")
        header.pack_start(self.search_entry)

        self.font_dropdown = Gtk.DropDown.new_from_strings([])
        self.font_dropdown.set_hexpand(False)
        self.font_dropdown.set_valign(Gtk.Align.CENTER)
        self.font_dropdown.set_tooltip_text("Select Nerd Font family to browse")
        header.pack_end(self.font_dropdown)

        # Loading spinner (hidden by default)
        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_spinning(False)
        self.loading_spinner.set_valign(Gtk.Align.CENTER)
        header.pack_end(self.loading_spinner)

        # Scroller + virtualized grid view
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        vbox.append(scroller)

        # Data model: base store -> filter -> selection
        self.base_store = Gio.ListStore(item_type=IconItem)
        self.filter_obj = Gtk.CustomFilter.new(self._filter_cb, None)
        self.filter_model = Gtk.FilterListModel(model=self.base_store, filter=self.filter_obj)
        self.selection = Gtk.NoSelection(model=self.filter_model)

        # Factory for cells
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._factory_setup)
        factory.connect("bind", self._factory_bind)
        factory.connect("teardown", self._factory_teardown)

        self.grid = Gtk.GridView(model=self.selection, factory=factory)
        self.grid.set_hexpand(True)
        self.grid.set_vexpand(True)
        self.grid.set_min_columns(1)
        self.grid.set_max_columns(0)
        scroller.set_child(self.grid)

        # Internal controller hooks
        self._on_search_changed: Optional[Callable[[str], None]] = None
        self._on_font_changed: Optional[Callable[[], None]] = None
        self._on_item_clicked: Optional[Callable[[IconItem], None]] = None

        # Bind UI events to controller when attached later
        self.search_entry.connect("search-changed", self._forward_search)
        self.font_dropdown.connect("notify::selected", self._forward_font_change)

        # Async scan generation for cancellation
        self._scan_generation = 0

    # Controller attachers
    def bind_handlers(
        self,
        on_search_changed: Callable[[str], None],
        on_font_changed: Callable[[], None],
        on_item_clicked: Callable[[IconItem], None],
    ):
        self._on_search_changed = on_search_changed
        self._on_font_changed = on_font_changed
        self._on_item_clicked = on_item_clicked

    # UI helpers exposed to controller
    def set_family_list(self, families: list[str]):
        self.family_list = list(families)
        self.font_dropdown.set_model(Gtk.StringList.new(self.family_list))

    def set_selected_font(self, name: Optional[str]):
        self.current_font = name
        if name and name in self.family_list:
            idx = self.family_list.index(name)
            self.font_dropdown.set_selected(idx)
        else:
            self.font_dropdown.set_selected(Gtk.INVALID_LIST_POSITION)

    def get_selected_font(self) -> Optional[str]:
        idx = self.font_dropdown.get_selected()
        if 0 <= idx < len(self.family_list):
            return self.family_list[idx]
        return None

    def set_loading(self, loading: bool):
        try:
            self.loading_spinner.set_spinning(loading)
            self.loading_spinner.set_visible(loading)
            self.font_dropdown.set_sensitive(not loading)
        except Exception:
            pass

    def set_search_text(self, text: str):
        self.search_text = (text or "").strip().lower()
        try:
            self.filter_obj.changed(Gtk.FilterChange.DIFFERENT)
        except Exception:
            pass

    def set_name_mapping(self, mapping: dict[int, str]):
        self.name_by_cp = mapping or {}

    def clear_items(self):
        try:
            n = self.base_store.get_n_items()
            if n:
                self.base_store.splice(0, n, [])
        except Exception:
            for i in range(self.base_store.get_n_items() - 1, -1, -1):
                self.base_store.remove(i)

    def get_generation(self) -> int:
        return self._scan_generation

    def next_generation(self) -> int:
        self._scan_generation += 1
        return self._scan_generation

    def append_codepoints(self, batch_cps: list[int], gen: int, is_last: bool):
        if gen != self._scan_generation:
            return False
        items = []
        for cp in batch_cps:
            name = self.name_by_cp.get(cp) or f"Glyph {cp:04X}"
            items.append(IconItem(name, cp))
        try:
            pos = self.base_store.get_n_items()
            self.base_store.splice(pos, 0, items)
        except Exception:
            for it in items:
                self.base_store.append(it)
        if is_last:
            self.set_loading(False)
            try:
                self.filter_obj.changed(Gtk.FilterChange.DIFFERENT)
            except Exception:
                pass
        return False

    def copy_to_clipboard(self, text: str, toast_message: str | None = None):
        display = Gdk.Display.get_default()
        if not display:
            if toast_message:
                self.show_toast("Failed to access display clipboard")
            return
        clipboard = display.get_clipboard()
        ok = False
        try:
            # 1) Best: native convenience if present
            if hasattr(clipboard, "set_text"):
                clipboard.set_text(text)
                ok = True
            else:
                # 2) Provide a generic value provider for str (lets GTK advertise text targets)
                provider = None
                try:
                    provider = Gdk.ContentProvider.new_for_value(text)
                except Exception:
                    provider = None

                # 3) Fallback: explicit bytes providers for common text MIME types
                if provider is None:
                    b = GLib.Bytes.new(text.encode("utf-8"))
                    providers = []
                    try:
                        providers.append(Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", b))
                    except Exception:
                        pass
                    try:
                        providers.append(Gdk.ContentProvider.new_for_bytes("text/plain", b))
                    except Exception:
                        pass
                    if providers:
                        try:
                            if hasattr(Gdk.ContentProvider, "new_union"):
                                provider = Gdk.ContentProvider.new_union(providers)
                        except Exception:
                            provider = None
                        if provider is None:
                            provider = providers[0]

                if provider is not None:
                    if hasattr(clipboard, "set"):
                        clipboard.set(provider)
                        ok = True
                    if hasattr(clipboard, "set_content"):
                        # set_content returns bool in some versions
                        try:
                            ok = bool(clipboard.set_content(provider)) or ok
                        except Exception:
                            pass
        except Exception:
            ok = False

        if not toast_message:
            return

        # Verify after a short delay to allow Wayland roundtrip
        def _verify_later():
            def _verify_cb(cb, res):
                try:
                    txt = cb.read_text_finish(res)
                    if isinstance(txt, str) and txt == text:
                        self.show_toast(toast_message)
                    else:
                        self.show_toast("Failed to copy to clipboard")
                except Exception:
                    self.show_toast("Failed to copy to clipboard" if not ok else toast_message)
                return False
            try:
                clipboard.read_text_async(None, _verify_cb)
            except Exception:
                self.show_toast("Failed to copy to clipboard" if not ok else toast_message)
            return False

        try:
            GLib.timeout_add(250, _verify_later)
        except Exception:
            # Fallback: immediate verification
            _verify_later()

    def show_toast(self, message: str):
        try:
            toast = Adw.Toast.new(message)
            self.toast_overlay.add_toast(toast)
        except Exception:
            pass

    # Internal wiring
    def _load_css(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        css_path = os.path.join(root, "data", "gtk.css")
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(css_path)
        except Exception:
            provider.load_from_data(b".glyph{font-size:32px}.tile{padding:8px}")
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _forward_search(self, entry: Gtk.SearchEntry):
        if self._on_search_changed:
            self._on_search_changed(entry.get_text() or "")

    def _forward_font_change(self, *_):
        if self._on_font_changed:
            self._on_font_changed()

    # Filtering callback used by Gtk.CustomFilter
    def _filter_cb(self, item: IconItem, _data=None) -> bool:
        if not self.search_text:
            return True
        try:
            hay = f"{item.name} U+{item.codepoint:04X} {item.codepoint:04x}".lower()
        except Exception:
            return True
        return self.search_text in hay

    # Cell factory setup/bind/teardown
    def _factory_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem):
        button = Gtk.Button()
        button.add_css_class("card")
        button.add_css_class("tile")
        button.set_size_request(120, 120)
        button.set_hexpand(False)
        button.set_vexpand(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        box.set_hexpand(True)
        box.set_vexpand(True)
        button.set_child(box)

        glyph = Gtk.Label()
        glyph.add_css_class("glyph")
        glyph.set_halign(Gtk.Align.CENTER)
        box.append(glyph)

        name_label = Gtk.Label()
        name_label.set_wrap(False)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_width_chars(14)
        name_label.set_max_width_chars(14)
        name_label.set_halign(Gtk.Align.CENTER)
        box.append(name_label)

        code_label = Gtk.Label()
        code_label.add_css_class("dim-label")
        code_label.set_wrap(False)
        code_label.set_halign(Gtk.Align.CENTER)
        box.append(code_label)

        # Store refs for fast bind
        list_item._button = button
        list_item._glyph = glyph
        list_item._name = name_label
        list_item._code = code_label

        # Click handled through controller
        button.connect("clicked", self._handle_item_click, list_item)

        list_item.set_child(button)

    def _factory_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem):
        item = list_item.get_item()
        if not isinstance(item, IconItem):
            return
        list_item._glyph.set_label(item.char())
        try:
            if self.current_font:
                attrs = Pango.AttrList()
                attrs.insert(Pango.attr_family_new(self.current_font))
                list_item._glyph.set_attributes(attrs)
        except Exception:
            pass
        list_item._name.set_label(item.name)
        list_item._code.set_label(item.code_hex())
        list_item._button.set_tooltip_text(f"Click to copy {item.name} {item.code_hex()}")

    def _factory_teardown(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem):
        for attr in ("_glyph", "_name", "_code", "_button"):
            if hasattr(list_item, attr):
                setattr(list_item, attr, None)

    def _handle_item_click(self, _button: Gtk.Button, list_item: Gtk.ListItem):
        if not self._on_item_clicked:
            return
        item = list_item.get_item()
        if isinstance(item, IconItem):
            self._on_item_clicked(item)
