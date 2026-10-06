"""Render the TypeFast UI to PNG files without showing anything on screen.

Usage: python scripts/render_previews.py [output_dir]
Uses made-up example words and a blank settings file, never the user's own data.
"""

import copy
import sys
import tempfile
import time
from pathlib import Path

import typefast  # noqa: F401  (pins GTK 3)
from gi.repository import Gtk

from typefast import config, windows
from typefast.hud import Hud
from typefast.overlay import Overlay
from typefast.vocab import Vocabulary

EXAMPLE_WORDS = ("OroCommerce, Akeneo, AssemblyAI, Kubernetes, PostgreSQL, Grafana, Prometheus, TypeScript, GraphQL, "
                 "Next.js, Tailwind, Supabase, Vercel, Cloudflare, Docker, Redis, Fly.io, Figma, Notion, Linear, Jira, "
                 "Confluence, Salesforce, SAP, Shopify, Magento, Odoo, Las Condes, Santiago, Valparaíso, Providencia, "
                 "Ñuñoa, Évora, Lisboa, Montréal, Zürich, Amélie Dubois, José Núñez, Ana Lima, RFQ, B2B, SKU, KPI, "
                 "Webhook, OAuth, Sprint, Roadmap, Backlog")
TICKED = ("Akeneo", "OroCommerce", "Las Condes")


def pump(seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.02)


def snapshot(widget, path: Path, width: int, after_show=None, glass: bool = True) -> None:
    parent = widget.get_parent()
    if parent is not None:
        parent.remove(widget)
    off = Gtk.OffscreenWindow()
    if glass:
        visual = off.get_screen().get_rgba_visual()
        if visual:
            off.set_visual(visual)
        off.get_style_context().add_class("tf-window")
    frame = Gtk.Box()
    margin = 24 if glass else 0
    for side in ("top", "bottom", "start", "end"):
        getattr(frame, f"set_margin_{side}")(margin)
    frame.pack_start(widget, True, True, 0)
    off.add(frame)
    off.set_size_request(width + 2 * margin, -1)
    off.show_all()
    if after_show:
        after_show()  # show_all() revealed widgets the real window keeps hidden
    pump()
    off.get_pixbuf().savev(str(path), "png", [], [])
    off.destroy()
    print("wrote", path)


def overlay(vocab, keys=(), typed="") -> Overlay:
    ov = Overlay(["fr", "es", "en"], "es", vocab, "Ctrl ×3", on_start=lambda *a: None, on_close=lambda: None)
    for name in keys:
        ov.model.key(name, "", time.monotonic())
    for i, ch in enumerate(typed):
        ov.model.key(ch, ch, 100 + i * 0.1)
    if ov.model.changed:
        ov.model.changed = False
        ov._build_cells()
    ov._render()
    return ov


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    vocab = Vocabulary(tmp / "v.json")
    vocab.add(EXAMPLE_WORDS)
    for w in TICKED:
        vocab.toggle(w)

    ov = overlay(vocab)
    width = ov.win.get_child().get_size_request()[0]
    snapshot(ov.win.get_child(), out / "overlay.png", width, ov._render)
    ov = overlay(vocab, keys=("Up", "Right"))
    snapshot(ov.win.get_child(), out / "overlay-style.png", width, ov._render)
    ov = overlay(vocab, typed="cond")
    snapshot(ov.win.get_child(), out / "overlay-vocabulary.png", width, ov._render)
    ov = overlay(vocab, typed="Zephyr")
    snapshot(ov.win.get_child(), out / "overlay-new-word.png", width, ov._render)

    class FakeSession:
        elapsed, level = 24.0, 0.55

    hud = Hud()
    hud.show_listening(FakeSession(), "es", "Ctrl ×3", 3)
    hud.set_live_text("Revisé el roadmap de OroCommerce con Ana esta mañana. El conector de Akeneo debería "
                      "estar listo a fin de mes. podemos agendar treinta minutos el jueves")
    hud.win.hide()
    snapshot(hud.card, out / "hud-listening.png", 640, lambda: hud._show("listening") or hud.win.hide())
    hud = Hud()
    hud.show_done("Formatted · 86 words", "Also in your clipboard.")
    hud.win.hide()
    snapshot(hud.card, out / "hud-done.png", 640,
             lambda: hud._show("done") or hud.timer.set_visible(False) or hud.win.hide())

    # Settings and vocabulary windows: built normally, but never shown on screen.
    windows.bring_to_front = lambda win: None
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["languages"] = ["en", "fr", "es"]
    settings = windows.SettingsWindow(cfg, True, lambda *a: None)
    snapshot(settings.win.get_child(), out / "settings.png", 700, glass=False)
    vocab_win = windows.VocabularyWindow(vocab)
    vocab_win.win.get_child().set_size_request(520, 600)
    snapshot(vocab_win.win.get_child(), out / "vocabulary.png", 520, glass=False)


if __name__ == "__main__":
    main()
