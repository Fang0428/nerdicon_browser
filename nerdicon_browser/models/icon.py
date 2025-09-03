import gi
gi.require_version("GObject", "2.0")
from gi.repository import GObject


class IconItem(GObject.GObject):
    name = GObject.Property(type=str)
    codepoint = GObject.Property(type=int)

    def __init__(self, name: str, codepoint: int):
        super().__init__()
        self.name = name
        self.codepoint = codepoint

    def char(self) -> str:
        try:
            return chr(self.codepoint)
        except Exception:
            return "?"

    def code_hex(self) -> str:
        return f"U+{self.codepoint:04X}"

