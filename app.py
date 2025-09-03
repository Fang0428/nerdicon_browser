#!/usr/bin/env python3
import sys

import gi
gi.require_version("Adw", "1")
from gi.repository import Adw

from nerdicon_browser.views.main_window import IconBrowserWindow
from nerdicon_browser.controllers.browser_controller import BrowserController


APP_ID = "com.example.NerdFontBrowser"


class IconBrowserApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=0)
        Adw.init()
        self.connect("activate", self.on_activate)
        self._controller = None

    def on_activate(self, app):
        win = IconBrowserWindow(self)
        self._controller = BrowserController(win)
        win.present()


def main(argv=None):
    app = IconBrowserApp()
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
