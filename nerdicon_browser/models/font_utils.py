import os
import subprocess
from typing import Dict, List, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk

try:
    from fontTools.ttLib import TTFont  # type: ignore
except Exception:
    TTFont = None  # type: ignore


def resolve_font_file_for_family(family_name: str) -> Optional[str]:
    try:
        res = subprocess.run(
            ["fc-match", "-f", "%{file}\n", family_name],
            capture_output=True, text=True, check=True
        )
        path = (res.stdout or "").strip().splitlines()[0] if res.stdout else ""
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


def read_glyph_names_from_font(path: str) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    if TTFont is None:
        return mapping
    try:
        tt = TTFont(path)
        best = tt.getBestCmap() or {}
        for cp, gname in best.items():
            mapping[int(cp)] = str(gname)
    except Exception:
        pass
    return mapping


def candidate_font_families() -> List[str]:
    try:
        tmp = Gtk.Label()
        ctx = tmp.get_pango_context()
        families = ctx.list_families()
    except Exception:
        return []
    names = [f.get_name() for f in families]
    nerdy = [n for n in names if "nerd" in n.lower() or "symbols nerd" in n.lower()]
    if nerdy:
        return sorted(dict.fromkeys(nerdy))
    preferred = {"Symbols Nerd Font", "Symbols Nerd Font Mono", "Nerd Font", "Monospace", "monospace"}
    avail = [n for n in names if n in preferred]
    return sorted(dict.fromkeys(avail or names))


    
