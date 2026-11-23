"""Eiendomskart: the generated map of one property (boundary + soil sample points), and fetching maps.

Fetching runs in a worker thread (network only, no database), so the window stays responsive.
Without internet the user gets a message; nothing else in the program depends on the map.
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

from .. import farm_map, map_service, repo
from ..farm_map import BACKGROUNDS, DEFAULT_BACKGROUND, FarmMap, MapUnavailable
from ..models import Property
from ..units import format_date
from .widgets import GREY, RED, show_errors

if TYPE_CHECKING:
    from ..plan_view import PlanView
    from .main_window import MainWindow

log = logging.getLogger("gjodselplan")
SAMPLE_COLOUR, FIELD_COLOUR, BOUNDARY_COLOUR = "#ffd400", "#1565c0", "#d0021b"


def run_in_background(parent: tk.Misc, text: str, work: Callable[[], object]) -> object:
    """Run `work` in a thread behind a small modal progress window; re-raise its exception here."""
    win = tk.Toplevel(parent)
    win.title("Vent litt")
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    ttk.Label(win, text=text, padding=(16, 12, 16, 6)).pack()
    bar = ttk.Progressbar(win, mode="indeterminate", length=260)
    bar.pack(padx=16, pady=(0, 14))
    bar.start(12)
    win.protocol("WM_DELETE_WINDOW", lambda: None)  # wait for the thread; it has a timeout
    box: dict[str, object] = {}

    def target():
        try:
            box["result"] = work()
        except BaseException as exc:  # noqa: BLE001 - handed back to the UI thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    done = tk.BooleanVar(win, False)

    def poll():
        if thread.is_alive():
            win.after(100, poll)
        else:
            done.set(True)

    try:
        win.grab_set()
    except tk.TclError:
        pass
    thread.start()
    win.after(50, poll)  # first check inside wait_variable: a quick failure must still end the wait
    win.wait_variable(done)
    win.grab_release()
    win.destroy()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("result")


def fetch_property_map(parent: tk.Misc, app: MainWindow, prop: Property, background_kind: str) -> FarmMap | None:
    """Fetch one property's map with a progress window. Shows the problem and returns None if it fails."""
    points, _missing = map_service.property_points(app.conn, prop.enterprise_id, prop.id)
    try:
        return run_in_background(  # type: ignore[return-value]
            parent, f"Henter kart for {prop.label} fra Kartverket og NIBIO …",
            lambda: map_service.fetch(app.paths.data_dir, prop, points, background_kind))
    except MapUnavailable as exc:
        show_errors(parent, [str(exc)], "Kart")
    except Exception as exc:  # noqa: BLE001 - never let the map break the program
        log.exception("Kart kunne ikke hentes")
        show_errors(parent, [f"Kartet kunne ikke lages: {exc}"], "Kart")
    return None


def offer_missing_maps(parent: tk.Misc, app: MainWindow, view: PlanView) -> bool:
    """Before export/finalising a draft: offer to fetch maps that are missing. True if any was fetched."""
    if view.finished:
        return False
    missing = [p for p in view.properties if not p.get("generated_map")]
    if not missing:
        return False
    names = "\n".join(f"• {p['label']}" for p in missing)
    if not messagebox.askyesno(
        "Eiendomskart",
        f"Eiendomskart med jordprøvepunkter er ikke hentet for:\n{names}\n\n"
        "Hente nå? Det krever internett (Kartverket og NIBIO). Svarer du nei, brukes bare opplastede kart.",
        parent=parent,
    ):
        return False
    fetched = False
    for p in missing:
        prop = repo.get_property(app.conn, p["id"])
        if prop is not None and fetch_property_map(parent, app, prop, DEFAULT_BACKGROUND) is not None:
            fetched = True
        elif prop is not None:
            break  # most likely offline: don't repeat the same error for every property
    return fetched


class MapDialog(tk.Toplevel):
    """Shows the cached map of a property with the current samples and fields; fetches it on request."""

    def __init__(self, master, app: MainWindow, prop: Property):
        super().__init__(master)
        self.app, self.prop = app, prop
        self.title(f"Eiendomskart – {prop.label}")
        self.transient(master.winfo_toplevel())
        self.geometry("900x680")
        self.minsize(560, 420)
        self.fm = farm_map.load_cached(app.paths.data_dir, prop.enterprise_id, prop.id)
        self._photo: tk.PhotoImage | None = None
        self._scaled_key: tuple | None = None

        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Bakgrunn:").pack(side="left")
        self.kind = tk.StringVar(value=(self.fm.background_kind if self.fm else DEFAULT_BACKGROUND))
        self._kind_labels = {label: code for code, label in BACKGROUNDS.items()}
        self.kind_combo = ttk.Combobox(top, state="readonly", width=34, values=list(self._kind_labels))
        self.kind_combo.set(BACKGROUNDS[self.kind.get()])
        self.kind_combo.pack(side="left", padx=(4, 8))
        self.fetch_button = ttk.Button(top, command=self.fetch)
        self.fetch_button.pack(side="left")
        ttk.Button(top, text="Lukk", command=self.destroy).pack(side="right")
        self.status = ttk.Label(top, foreground=GREY)
        self.status.pack(side="left", padx=(10, 0))

        self.canvas = tk.Canvas(self, background="#eeeeee", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.notes = ttk.Label(self, justify="left", wraplength=860, padding=(8, 4, 8, 0))
        self.notes.pack(fill="x")
        self.attribution = ttk.Label(self, foreground=GREY, padding=(8, 0, 8, 8))
        self.attribution.pack(fill="x")
        self.bind("<Escape>", lambda _e: self.destroy())
        self._update_texts()

    # --- Fetching -----------------------------------------------------------------------------

    def fetch(self) -> None:
        kind = self._kind_labels.get(self.kind_combo.get(), DEFAULT_BACKGROUND)
        fm = fetch_property_map(self, self.app, self.prop, kind)
        if fm is not None and self.winfo_exists():
            self.fm = fm
            self._scaled_key = None
            self._update_texts()
            self.redraw()
            self.app.data_changed()

    # --- Drawing ------------------------------------------------------------------------------

    def _update_texts(self) -> None:
        points, missing = map_service.property_points(self.app.conn, self.prop.enterprise_id, self.prop.id)
        self.points, self.missing = points, missing
        self.fetch_button.configure(text="Hent på nytt" if self.fm else "Hent kart")
        if self.fm is None:
            self.status.configure(text="Ikke hentet. Kartet krever internett.")
            self.attribution.configure(text="")
        else:
            self.status.configure(text=f"Hentet {format_date(self.fm.fetched_date())} · "
                                       f"{BACKGROUNDS.get(self.fm.background_kind, '')}")
            self.attribution.configure(text=farm_map.ATTRIBUTION.get(self.fm.background_kind, ""))
        lines = []
        if self.fm is not None:
            lines += self.fm.notes
            _inside, outside = self.fm.place(points)
            if outside:
                lines.append("Utenfor kartutsnittet – trykk «Hent på nytt»: " + ", ".join(p.label for p in outside))
            away = self.fm.teiger_outside()
            if away:
                lines.append(f"{away} teig{'er' if away > 1 else ''} av eiendommen ligger utenfor kartutsnittet.")
        if missing:
            lines.append("Jordprøver uten koordinater (§ 29): " + ", ".join(missing))
        if not points:
            lines.append("Ingen jordprøver eller skifter på eiendommen har koordinater ennå.")
        self.notes.configure(text="\n".join(lines), foreground=RED if self.missing or (
            self.fm is not None and self.fm.place(points)[1]) else "")

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 10), max(c.winfo_height(), 10)
        if self.fm is None:
            c.create_text(cw / 2, ch / 2, text="Ingen kart hentet ennå.\nTrykk «Hent kart» (krever internett).",
                          justify="center", fill="#555555")
            return
        scale = min(cw / self.fm.width, ch / self.fm.height)
        w, h = max(1, int(self.fm.width * scale)), max(1, int(self.fm.height * scale))
        ox, oy = (cw - w) / 2, (ch - h) / 2
        photo = self._background(w, h)
        if photo is None:
            c.create_text(cw / 2, ch / 2, text="Kartbildet kunne ikke leses. Trykk «Hent på nytt».", fill=RED)
            return
        c.create_image(ox, oy, image=photo, anchor="nw")
        for ring in self.fm.rings():
            coords = [v for fx, fy in ring for v in (ox + fx * w, oy + fy * h)]
            if len(coords) >= 4:
                c.create_line(*coords, fill=BOUNDARY_COLOUR, width=2)
        inside, _outside = self.fm.place(self.points)
        for point, fx, fy in sorted(inside, key=lambda item: item[0].kind == "sample"):
            x, y = ox + fx * w, oy + fy * h
            if point.kind == "sample":
                c.create_oval(x - 5, y - 5, x + 5, y + 5, fill=SAMPLE_COLOUR, outline="black", width=1)
                label = c.create_text(x + 8, y, text=point.label, anchor="w", font=("TkDefaultFont", 9, "bold"))
            else:
                c.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill=FIELD_COLOUR, outline="white")
                label = c.create_text(x, y + 7, text=point.label, anchor="n", fill="#0d3c78")
            x1, y1, x2, y2 = c.bbox(label)
            if x2 > ox + w:  # keep labels inside the map
                c.move(label, ox + w - x2 - 2, 0)
                x1, y1, x2, y2 = c.bbox(label)
            back = c.create_rectangle(x1 - 2, y1 - 1, x2 + 2, y2 + 1, fill="white", outline="")
            c.tag_lower(back, label)
        metres = farm_map.nice_scale_length(self.fm.metres_per_width())
        bar = metres / self.fm.metres_per_width() * w
        c.create_rectangle(ox + 6, oy + h - 26, ox + bar + 18, oy + h - 6, fill="white", outline="")
        c.create_line(ox + 12, oy + h - 11, ox + 12 + bar, oy + h - 11, width=3)
        c.create_text(ox + 12, oy + h - 15, text=f"{metres} m", anchor="sw", font=("TkDefaultFont", 8))
        c.create_text(ox + w - 12, oy + 10, text="▲\nN", anchor="n", justify="center", font=("TkDefaultFont", 9, "bold"))
        c.create_rectangle(ox, oy, ox + w, oy + h, outline="#555555")

    def _background(self, w: int, h: int) -> tk.PhotoImage | None:
        key = (self.fm.background, w, h)
        if key == self._scaled_key and self._photo is not None:
            return self._photo
        try:
            from PIL import Image

            image = Image.open(self.app.paths.data_dir / self.fm.background).convert("RGB").resize((w, h))
            out = io.BytesIO()
            image.save(out, "PNG")
            self._photo = tk.PhotoImage(master=self, data=base64.b64encode(out.getvalue()).decode("ascii"))
        except Exception:  # noqa: BLE001 - damaged or missing image: the user can fetch again
            log.exception("Kartbildet kunne ikke leses")
            return None
        self._scaled_key = key
        return self._photo
