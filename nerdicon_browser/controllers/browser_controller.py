import threading
from typing import Dict

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from nerdicon_browser.models import (
    candidate_font_families,
    resolve_font_file_for_family,
    read_glyph_names_from_font,
    IconItem,
)


class BrowserController:
    def __init__(self, view):
        self.view = view
        self._names_cache: Dict[str, Dict[int, str]] = {}

        # Bind view event handlers
        self.view.bind_handlers(
            on_search_changed=self.on_search_changed,
            on_font_changed=self.on_font_changed,
            on_item_clicked=self.on_item_clicked,
        )

        # Initialize font list and first scan
        families = candidate_font_families()
        self.view.set_family_list(families)
        if families:
            self.view.set_selected_font(families[0])
            self.rebuild_from_font()

    def on_search_changed(self, text: str):
        self.view.set_search_text(text)

    def on_font_changed(self):
        name = self.view.get_selected_font()
        self.view.set_selected_font(name)
        self.rebuild_from_font()

    def on_item_clicked(self, item: IconItem):
        text = item.char()
        self.view.copy_to_clipboard(text, f"Copied {item.name} {item.code_hex()}")

    def rebuild_from_font(self):
        current_font = self.view.get_selected_font()
        if not current_font:
            return
        gen = self.view.next_generation()
        self.view.clear_items()
        self.view.set_loading(True)

        # Load names in a background thread (safe), then start scanning in main loop
        def load_names_then_scan():
            try:
                names_map: Dict[int, str] = {}
                if current_font in self._names_cache:
                    names_map = self._names_cache[current_font]
                else:
                    path = resolve_font_file_for_family(current_font)
                    if path:
                        names_map = read_glyph_names_from_font(path)
                    self._names_cache[current_font] = names_map
            except Exception:
                names_map = {}

            def start_scan_main():
                # If user switched fonts, abort
                if gen != self.view.get_generation():
                    return False
                self.view.set_name_mapping(names_map or {})

                # Build Pango objects on main thread only
                try:
                    ctx = self.view.get_pango_context()
                    # We'll prepare coverage checks within scan function to stay on main thread
                except Exception:
                    self.view.set_loading(False)
                    return False

                # State for incremental scanning on main loop
                ranges = [
                    (0xE000, 0xF8FF),
                    (0xF0000, 0xF8FFF),
                    (0x10F000, 0x10F8FF),
                ]
                current_range_index = 0
                current_cp = ranges[0][0] if ranges else 0

                # Prepare Pango font + coverage in main thread
                try:
                    from gi.repository import Pango as _Pango
                    desc = _Pango.FontDescription.from_string(f"{current_font} 16")
                    fmap = ctx.get_font_map()
                    if not fmap:
                        raise RuntimeError("No font map")
                    font = fmap.load_font(ctx, desc)
                    if not font:
                        raise RuntimeError("Font not loaded")
                    try:
                        lang = _Pango.Language.get_default() if hasattr(_Pango.Language, "get_default") else None
                        coverage = font.get_coverage(lang)
                    except Exception:
                        coverage = None
                except Exception:
                    self.view.set_loading(False)
                    return False

                def covered(cp: int) -> bool:
                    if coverage is None:
                        try:
                            return font.has_char(cp)
                        except Exception:
                            return False
                    try:
                        level = coverage.get(cp)
                        return level and int(level) > 0
                    except Exception:
                        return False

                BATCH_CHECKS = 1024  # number of codepoints to examine per idle

                def scan_step():
                    nonlocal current_range_index, current_cp
                    if gen != self.view.get_generation():
                        return False
                    if current_range_index >= len(ranges):
                        # Finished: mark loading done
                        self.view.append_codepoints([], gen, True)
                        return False
                    start, end = ranges[current_range_index]
                    checked = 0
                    batch_cps = []
                    while checked < BATCH_CHECKS and current_cp <= end:
                        if covered(current_cp):
                            batch_cps.append(current_cp)
                        current_cp += 1
                        checked += 1

                    # If finished current range move to next
                    if current_cp > end:
                        current_range_index += 1
                        if current_range_index < len(ranges):
                            current_cp = ranges[current_range_index][0]

                    if batch_cps:
                        # Not last yet unless all ranges done and we know no more items
                        self.view.append_codepoints(batch_cps, gen, False)

                    # Continue scanning
                    return True

                # Kick off scanning
                GLib.idle_add(scan_step, priority=GLib.PRIORITY_DEFAULT_IDLE)
                return False

            GLib.idle_add(start_scan_main, priority=GLib.PRIORITY_DEFAULT_IDLE)

        threading.Thread(target=load_names_then_scan, daemon=True).start()
