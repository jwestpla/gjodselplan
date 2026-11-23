"""Coordinate picker for forms with «lat» and «lon» entries: Google Maps in the browser + paste."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable

from ..geo import (
    format_coordinates,
    google_maps_search_url,
    google_maps_url,
    is_short_link,
    outside_norway,
    parse_coordinates,
)
from ..models import Enterprise
from ..units import parse_number
from .widgets import AMBER, GREEN, RED, Form

HINT = ("Høyreklikk punktet i Google Maps og klikk på tallene øverst i menyen – da kopieres de. "
        "Trykk så «Lim inn koordinater» (eller lim inn i Breddegrad).")
GOOGLE_MAPS_HOME = "https://www.google.com/maps"

# Where to open the map when the form has no coordinates yet: a point, a search text, or None.
Fallback = Callable[[], "tuple[float, float] | str | None"]


def add_coordinate_picker(form: Form, column: int, fallback: Fallback) -> None:
    """Add «Åpne Google Maps» / «Lim inn koordinater» below the lat/lon entries of `form`.

    Pasting a coordinate pair into either entry (Ctrl+V) also fills both fields.
    """
    status = ttk.Label(form, text=HINT, wraplength=300, justify="left", foreground="#666666")
    buttons = ttk.Frame(form)
    ttk.Button(buttons, text="Åpne Google Maps", command=lambda: open_map(form, fallback)).pack(side="left")
    ttk.Button(buttons, text="Lim inn koordinater",
               command=lambda: paste_coordinates(form, status)).pack(side="left", padx=(6, 0))
    form.custom("coordinate_buttons", "Finn i kart", buttons, column)
    form.custom("coordinate_status", "", status, column)
    for key in ("lat", "lon"):
        form.widgets[key].bind("<<Paste>>", lambda _e: paste_coordinates(form, status, quiet=True))


def paste_coordinates(form: Form, status: ttk.Label, quiet: bool = False) -> str | None:
    """Fill lat/lon from the clipboard. Returns "break" when it did (stops the normal paste)."""
    try:
        text = form.clipboard_get()
    except tk.TclError:
        text = ""
    coords = parse_coordinates(text)
    if coords is None:
        if not quiet:
            if is_short_link(text):
                message = ("Kortlenker (maps.app.goo.gl) kan ikke leses. Høyreklikk punktet i kartet og "
                           "klikk på tallene i stedet.")
            else:
                message = "Fant ingen koordinater på utklippstavlen. " + HINT
            status.configure(text=message, foreground=RED)
        return None
    lat, lon = round(coords[0], 6), round(coords[1], 6)
    form.set("lat", lat)
    form.set("lon", lon)
    if outside_norway(lat, lon):
        status.configure(text=f"Satt: {format_coordinates(lat, lon)} – merk: punktet ligger utenfor Norge. "
                              "Er bredde og lengde byttet om?", foreground=AMBER)
    else:
        status.configure(text=f"Satt: {format_coordinates(lat, lon)}", foreground=GREEN)
    return "break"


def open_map(form: Form, fallback: Fallback) -> None:
    """Satellite view at the entered point; otherwise at the fallback point or search."""
    try:
        lat, lon = parse_number(form.get("lat")), parse_number(form.get("lon"))
    except ValueError:
        lat = lon = None
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        webbrowser.open(google_maps_url(lat, lon))
        return
    target = fallback()
    if isinstance(target, tuple):
        webbrowser.open(google_maps_url(*target))
    elif target:
        webbrowser.open(google_maps_search_url(target))
    else:
        webbrowser.open(GOOGLE_MAPS_HOME)


def address_query(e: Enterprise | None) -> str | None:
    """'Fruktvegen 1, 6887 Lærdal, Norge' from the enterprise address, or None."""
    if e is None:
        return None
    town = " ".join(filter(None, (e.postcode, e.place)))
    parts = [p for p in (e.street, town) if p]
    return ", ".join(parts + ["Norge"]) if parts else None
