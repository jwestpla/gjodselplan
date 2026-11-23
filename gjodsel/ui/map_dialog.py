"""Eiendomskart: the generated map of one property (boundary + soil sample points), and fetching maps.

Fetching runs in a worker thread (network only, no database), so the window stays responsive.
Without internet the user gets a message; nothing else in the program depends on the map.
"""

from __future__ import annotations

import base64
import gc
import io
import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

from .. import farm_map, map_service, repo
from ..farm_map import BACKGROUNDS, DEFAULT_BACKGROUND, PHOTO, FarmMap, MapUnavailable
from .. import gardskart
from ..gardskart import GardskartError
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
    # Tk objects must only be freed by the main thread; the cyclic garbage collector could otherwise run
    # (and free old Tk variables) inside the worker. Reference counting still frees everything else.
    collecting = gc.isenabled()
    gc.disable()
    try:
        thread.start()
        win.after(50, poll)  # first check inside wait_variable: a quick failure must still end the wait
        win.wait_variable(done)
    finally:
        if collecting:
            gc.enable()
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
    """Shows the cached map of a property with the current samples and fields; fetches it on request.

    The aerial photo from an uploaded gårdskart is a third choice: it is placed automatically by matching
    Kartverket's boundary, or by hand (drag the red boundary onto the photo) when the match is uncertain.
    """

    def __init__(self, master, app: MainWindow, prop: Property, start_kind: str | None = None,
                 fetch_now: bool = False):
        super().__init__(master)
        self.app, self.prop = app, prop
        self.align: dict | None = None  # alignment mode: {"dx", "dy", "attempt", "start"}
        self.title(f"Eiendomskart – {prop.label}")
        self.transient(master.winfo_toplevel())
        self.geometry("900x680")
        self.minsize(560, 420)
        self.fm = farm_map.load_cached(app.paths.data_dir, prop.enterprise_id, prop.id)
        self._photo: tk.PhotoImage | None = None
        self._scaled_key: tuple | None = None
        self._map_box = (0.0, 0.0, 1.0, 1.0)  # x, y, width, height of the drawn map on the canvas

        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Bakgrunn:").pack(side="left")
        kinds = [k for k in BACKGROUNDS if k != PHOTO or (prop.map_attachment or "").lower().endswith(".pdf")]
        self._kind_labels = {BACKGROUNDS[k]: k for k in kinds}
        self.kind_combo = ttk.Combobox(top, state="readonly", width=30, values=list(self._kind_labels))
        initial = start_kind if start_kind in kinds else (self.fm.background_kind if self.fm else DEFAULT_BACKGROUND)
        self.kind_combo.set(BACKGROUNDS[initial])
        self.kind_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_kind())
        self.kind_combo.pack(side="left", padx=(4, 8))
        self.fetch_button = ttk.Button(top, command=self.fetch)
        self.fetch_button.pack(side="left")
        self.adjust_button = ttk.Button(top, text="Juster plassering", command=self.start_alignment)
        ttk.Button(top, text="Lukk", command=self.destroy).pack(side="right")
        self.align_bar = ttk.Frame(self, padding=(8, 0, 8, 4))
        self.align_text = ttk.Label(self.align_bar, foreground=RED, wraplength=640, justify="left")
        self.align_text.pack(side="left", fill="x", expand=True)
        ttk.Button(self.align_bar, text="Avbryt", command=self.cancel_alignment).pack(side="right")
        ttk.Button(self.align_bar, text="Lagre plassering", command=self.save_alignment).pack(side="right", padx=6)
        self.status = ttk.Label(top, foreground=GREY)
        self.status.pack(side="left", padx=(10, 0))

        self.canvas = tk.Canvas(self, background="#eeeeee", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag)
        for key, (dx, dy) in {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}.items():
            self.bind(f"<{key}>", lambda _e, d=(dx, dy): self._nudge(*d, 1))
            self.bind(f"<Shift-{key}>", lambda _e, d=(dx, dy): self._nudge(*d, 10))
        self.notes = ttk.Label(self, justify="left", wraplength=860, padding=(8, 4, 8, 0))
        self.notes.pack(fill="x")
        self.attribution = ttk.Label(self, foreground=GREY, padding=(8, 0, 8, 8))
        self.attribution.pack(fill="x")
        self.bind("<Escape>", lambda _e: self.cancel_alignment() if self.align else self.destroy())
        self._update_texts()
        if fetch_now:
            self.after(50, self.fetch)

    # --- Fetching -----------------------------------------------------------------------------

    def _kind(self) -> str:
        return self._kind_labels.get(self.kind_combo.get(), DEFAULT_BACKGROUND)

    def _show(self, fm: FarmMap | None) -> None:
        self.fm = fm
        self._scaled_key = None
        self._update_texts()
        self.redraw()

    def _on_kind(self) -> None:
        """Switch between stored maps without network; otherwise ask for «Hent kart»."""
        if self.align:
            self.cancel_alignment()
        kind = self._kind()
        which = PHOTO if kind == PHOTO else farm_map.GENERATED
        cached = farm_map.load_cached(self.app.paths.data_dir, self.prop.enterprise_id, self.prop.id, which)
        if cached is not None and (kind == PHOTO or cached.background_kind == kind):
            farm_map.set_active(self.app.paths.data_dir, self.prop.enterprise_id, self.prop.id, which)
            self._show(cached)
            self.app.data_changed()
        else:
            self.status.configure(text=f"«{BACKGROUNDS[kind]}» er ikke laget ennå – trykk «Hent kart».")
            self.fetch_button.configure(text="Hent kart")

    def fetch(self) -> None:
        if self.align:
            self.cancel_alignment()
        kind = self._kind()
        if kind == PHOTO:
            self._fetch_photo()
            return
        fm = fetch_property_map(self, self.app, self.prop, kind)
        if fm is not None and self.winfo_exists():
            self._show(fm)
            self.app.data_changed()

    def _fetch_photo(self) -> None:
        prop = repo.get_property(self.app.conn, self.prop.id) or self.prop
        try:
            attempt = run_in_background(
                self, "Leser gårdskartet og plasserer flyfotoet (henter eiendomsgrensen fra Kartverket) …",
                lambda: map_service.prepare_photo_map(self.app.paths.data_dir, prop))
        except (MapUnavailable, GardskartError) as exc:
            show_errors(self, [str(exc)], "Flyfoto")
            return
        except Exception as exc:  # noqa: BLE001 - never let the map break the program
            log.exception("Flyfoto kunne ikke plasseres")
            show_errors(self, [f"Flyfotoet kunne ikke plasseres: {exc}"], "Flyfoto")
            return
        if not self.winfo_exists():
            return
        if attempt.georef.confident:
            self._show(map_service.save_photo_map(self.app.paths.data_dir, prop, attempt))
            self.app.data_changed()
            return
        g = attempt.georef
        if g.length_m < gardskart.MIN_LENGTH_M:
            why = f"bare {g.length_m:.0f} m av eiendomsgrensen er med i gårdskartet"
        elif g.rival is not None and g.score - g.rival < gardskart.MIN_MARGIN:
            why = "flere plasseringer passer omtrent like godt (f.eks. bare en rett grenselinje i kartet)"
        else:
            why = f"bare {g.score:.0%} av eiendomsgrensen passer med gårdskartet"
        self.fm = map_service.photo_farm_map(prop, attempt)
        self._scaled_key = None
        self._enter_alignment(attempt, f"Automatisk plassering er usikker: {why}. Sjekk plasseringen: dra den røde "
                                       "grensen til den ligger over eiendomsgrensen i flybildet (piltaster for små "
                                       "steg), og trykk «Lagre plassering».")

    # --- Aligning the aerial photo by hand ----------------------------------------------------------

    def start_alignment(self) -> None:
        if self.fm is not None and self.fm.background_kind == PHOTO:
            self._enter_alignment(None, "Dra den røde grensen til den ligger over eiendomsgrensen i flybildet "
                                        "(piltaster for små steg, Shift for større), og trykk «Lagre plassering».")

    def _enter_alignment(self, attempt, text: str) -> None:
        self.align = {"dx": 0.0, "dy": 0.0, "attempt": attempt, "start": None}
        self.align_text.configure(text=text)
        self.align_bar.pack(fill="x", before=self.canvas)
        self.fetch_button.state(["disabled"])
        self.adjust_button.state(["disabled"])
        self.redraw()

    def _leave_alignment(self, fm: FarmMap | None) -> None:
        # Set the map first: hiding the bar resizes the canvas, which redraws at once.
        self.align = None
        self.fm, self._scaled_key = fm, None
        self.align_bar.pack_forget()
        self.fetch_button.state(["!disabled"])
        self.adjust_button.state(["!disabled"])
        self._show(fm)

    def cancel_alignment(self) -> None:
        if self.align is not None:
            self._leave_alignment(farm_map.load_cached(self.app.paths.data_dir, self.prop.enterprise_id, self.prop.id))

    def _drag_start(self, event) -> None:
        if self.align is not None:
            self.align["start"] = (event.x, event.y)

    def _drag(self, event) -> None:
        if self.align is not None and self.align["start"] is not None:
            x0, y0 = self.align["start"]
            self._nudge(event.x - x0, event.y - y0, 1)
            self.align["start"] = (event.x, event.y)

    def _nudge(self, dx: float, dy: float, step: float) -> None:
        if self.align is not None:
            self.align["dx"] += dx * step
            self.align["dy"] += dy * step
            self.redraw()

    def alignment_metres(self) -> tuple[float, float]:
        """How far the photo must move (east, north) for the dragged boundary to fit."""
        metres_per_px = self.fm.metres_per_width() / max(self._map_box[2], 1)
        return -self.align["dx"] * metres_per_px, self.align["dy"] * metres_per_px

    def save_alignment(self) -> None:
        if self.align is None or self.fm is None:
            return
        d_e, d_n = self.alignment_metres()
        prop = repo.get_property(self.app.conn, self.prop.id) or self.prop
        attempt = self.align["attempt"]
        if attempt is not None:
            fm = map_service.save_photo_map(self.app.paths.data_dir, prop, attempt, attempt.georef.shifted(d_e, d_n))
        else:
            fm = map_service.save_photo_alignment(self.app.paths.data_dir, prop, self.fm, d_e, d_n)
        self._leave_alignment(fm)
        self.app.data_changed()

    # --- Drawing ------------------------------------------------------------------------------

    def _update_texts(self) -> None:
        points, missing = map_service.property_points(self.app.conn, self.prop.enterprise_id, self.prop.id)
        self.points, self.missing = points, missing
        self.fetch_button.configure(text="Hent på nytt" if self.fm else "Hent kart")
        photo = self.fm is not None and self.fm.background_kind == PHOTO
        if photo:
            self.adjust_button.pack(side="left", padx=(6, 0), after=self.fetch_button)
        else:
            self.adjust_button.pack_forget()
        if self.fm is None:
            self.status.configure(text="Ikke hentet. Kartet krever internett.")
            self.attribution.configure(text="")
        elif photo:
            g = self.fm.georef
            how = (f"plassert automatisk (treff {g.get('score', 0):.0%}, UTM {g.get('zone')})"
                   if g.get("method") == "auto" else "plassert manuelt")
            self.status.configure(text=f"Flyfoto fra gårdskart utskrevet {g.get('printed') or '?'} · {how}")
            self.attribution.configure(text=self.fm.credit())
        else:
            self.status.configure(text=f"Hentet {format_date(self.fm.fetched_date())} · "
                                       f"{BACKGROUNDS.get(self.fm.background_kind, '')}")
            self.attribution.configure(text=self.fm.credit())
        lines = []
        if photo and not map_service.photo_is_current(self.app.paths.data_dir, self.prop, self.fm):
            lines.append("Gårdskartet er byttet etter at flyfotoet ble plassert – trykk «Hent på nytt».")
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
        self._map_box = (ox, oy, w, h)
        photo = self._background(w, h)
        if photo is None:
            c.create_text(cw / 2, ch / 2, text="Kartbildet kunne ikke leses. Trykk «Hent på nytt».", fill=RED)
            return
        c.create_image(ox, oy, image=photo, anchor="nw")
        sx, sy = (self.align["dx"], self.align["dy"]) if self.align else (0, 0)
        for ring in self.fm.rings():
            coords = [v for fx, fy in ring for v in (ox + fx * w + sx, oy + fy * h + sy)]
            if len(coords) >= 4:
                c.create_line(*coords, fill=BOUNDARY_COLOUR, width=3 if self.align else 2)
        if self.align:
            c.create_rectangle(ox, oy, ox + w, oy + h, outline="#555555")
            return
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
        pending = self.align["attempt"] if self.align and self.align.get("attempt") else None
        key = (self.fm.background, id(pending), w, h)
        if key == self._scaled_key and self._photo is not None:
            return self._photo
        try:
            from PIL import Image

            source = io.BytesIO(pending.pdf.photo_jpeg) if pending else self.app.paths.data_dir / self.fm.background
            image = Image.open(source).convert("RGB").resize((w, h))
            out = io.BytesIO()
            image.save(out, "PNG")
            self._photo = tk.PhotoImage(master=self, data=base64.b64encode(out.getvalue()).decode("ascii"))
        except Exception:  # noqa: BLE001 - damaged or missing image: the user can fetch again
            log.exception("Kartbildet kunne ikke leses")
            return None
        self._scaled_key = key
        return self._photo
