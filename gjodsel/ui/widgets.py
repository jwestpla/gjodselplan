"""Reusable form, table and dialog helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Iterable, Sequence

from ..units import format_plain, parse_number
from ..validation import ValidationError

RED = "#b00020"
AMBER = "#a15c00"
GREEN = "#1b7a2f"
GREY = "#888888"
BLUE = "#1f5fbf"

Choices = Sequence[tuple[str, str]]  # (stored code, displayed label)


def show_errors(parent: tk.Misc, errors: list[str], title: str = "Kan ikke lagre") -> None:
    text = errors[0] if len(errors) == 1 else "\n".join(f"• {e}" for e in errors)
    messagebox.showerror(title, text, parent=parent)


def scrolled_tree(parent: tk.Misc, columns: Sequence[tuple[str, str, int]], height: int = 8,
                  numeric: Iterable[str] = (), xscroll: bool = False) -> tuple[ttk.Frame, ttk.Treeview]:
    """A headings-only Treeview with scrollbars. Numeric columns are right-aligned."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, show="headings", height=height, selectmode="browse")
    configure_columns(tree, columns, numeric)
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=ys.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    if xscroll:
        xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=xs.set)
        xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


def configure_columns(tree: ttk.Treeview, columns: Sequence[tuple[str, str, int]],
                      numeric: Iterable[str] = ()) -> None:
    numeric = set(numeric)
    tree["columns"] = [c[0] for c in columns]
    for key, heading, width in columns:
        anchor = "e" if key in numeric else "w"
        tree.heading(key, text=heading, anchor=anchor)
        tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=True)


STRIPE = "#f2f4f1"


def stripe(tree: ttk.Treeview, skip: Iterable[str] = ()) -> None:
    """Shade every other row. Rows carrying a tag in `skip` keep their own background."""
    skip = set(skip)
    tree.tag_configure("stripe", background=STRIPE)
    for index, iid in enumerate(tree.get_children()):
        tags = [t for t in tree.item(iid, "tags") if t != "stripe"]
        if index % 2 and not skip & set(tags):
            tags.append("stripe")
        tree.item(iid, tags=tags)


def selected_id(tree: ttk.Treeview) -> int | None:
    selection = tree.selection()
    return int(selection[0]) if selection else None


def button_row(parent: tk.Misc, specs: Sequence[tuple[str, Callable[[], None]]]) -> ttk.Frame:
    frame = ttk.Frame(parent)
    for text, command in specs:
        ttk.Button(frame, text=text, command=command).pack(side="left", padx=(0, 6))
    return frame


class Form(ttk.Frame):
    """Grid of labelled inputs in one or more column groups.

    Values are read and written as strings (or bool for checkboxes); combos
    map between stored codes and displayed labels. Numbers are parsed with
    get_number(), which accepts decimal comma or point.
    """

    def __init__(self, master: tk.Misc, columns: int = 1, **kw):
        super().__init__(master, **kw)
        self._vars: dict[str, tk.Variable] = {}
        self._labels: dict[str, str] = {}
        self._choices: dict[str, list[tuple[str, str]]] = {}
        self._texts: dict[str, tk.Text] = {}
        self.widgets: dict[str, tk.Widget] = {}
        self._next_row = [0] * columns

    def _place(self, key: str, label: str, widget: tk.Widget, column: int, sticky: str = "w") -> None:
        row = self._next_row[column]
        self._next_row[column] += 1
        ttk.Label(self, text=label).grid(
            row=row, column=column * 2, sticky="nw", padx=(16 if column else 0, 6), pady=2
        )
        widget.grid(row=row, column=column * 2 + 1, sticky=sticky, pady=2)
        self._labels[key] = label.rstrip(" *")
        self.widgets[key] = widget

    def entry(self, key: str, label: str, width: int = 30, column: int = 0) -> ttk.Entry:
        var = self._vars[key] = tk.StringVar()
        widget = ttk.Entry(self, textvariable=var, width=width)
        self._place(key, label, widget, column)
        return widget

    def combo(self, key: str, label: str, choices: Choices, width: int = 28, column: int = 0,
              readonly: bool = True) -> ttk.Combobox:
        var = self._vars[key] = tk.StringVar()
        widget = ttk.Combobox(self, textvariable=var, width=width, state="readonly" if readonly else "normal")
        self._place(key, label, widget, column)
        self.set_choices(key, choices)
        return widget

    def check(self, key: str, label: str, column: int = 0) -> ttk.Checkbutton:
        var = self._vars[key] = tk.BooleanVar()
        widget = ttk.Checkbutton(self, variable=var)
        self._place(key, label, widget, column)
        return widget

    def text(self, key: str, label: str, height: int = 3, width: int = 40, column: int = 0) -> tk.Text:
        widget = tk.Text(self, height=height, width=width, wrap="word", font="TkDefaultFont")
        self._texts[key] = widget
        self._place(key, label, widget, column, sticky="ew")
        return widget

    def entries(self, label: str, items: Sequence[tuple[str, str]], width: int = 7, column: int = 0) -> ttk.Frame:
        """Several short entries on one row, e.g. N / P / K. items: (key, caption before the entry)."""
        frame = ttk.Frame(self)
        for i, (key, caption) in enumerate(items):
            ttk.Label(frame, text=caption).pack(side="left", padx=(0 if i == 0 else 8, 3))
            var = self._vars[key] = tk.StringVar()
            entry = ttk.Entry(frame, textvariable=var, width=width)
            entry.pack(side="left")
            self.widgets[key] = entry
            self._labels[key] = f"{label} {caption}"
        self._place(f"_row_{items[0][0]}", label, frame, column)
        return frame

    def custom(self, key: str, label: str, widget: tk.Widget, column: int = 0, sticky: str = "w") -> tk.Widget:
        """Place any widget created with this form as its master (e.g. a row of buttons)."""
        self._place(key, label, widget, column, sticky)
        return widget

    def label(self, key: str, label: str, column: int = 0) -> ttk.Label:
        var = self._vars[key] = tk.StringVar()
        widget = ttk.Label(self, textvariable=var)
        self._place(key, label, widget, column)
        return widget

    def set_choices(self, key: str, choices: Choices) -> None:
        self._choices[key] = list(choices)
        self.widgets[key]["values"] = [label for _, label in choices]

    def var(self, key: str) -> tk.Variable:
        return self._vars[key]

    def get(self, key: str):
        if key in self._texts:
            return self._texts[key].get("1.0", "end-1c")
        value = self._vars[key].get()
        if key in self._choices:
            for code, label in self._choices[key]:
                if label == value:
                    return code
        return value

    def set(self, key: str, value) -> None:
        if key in self._texts:
            widget = self._texts[key]
            state = widget.cget("state")
            widget.configure(state="normal")  # a disabled Text ignores edits
            widget.delete("1.0", "end")
            widget.insert("1.0", value or "")
            widget.configure(state=state)
            return
        var = self._vars[key]
        if isinstance(var, tk.BooleanVar):
            var.set(bool(value))
            return
        if key in self._choices:
            value = next((label for code, label in self._choices[key] if code == value), value)
        var.set(format_plain(value) if isinstance(value, (int, float)) else (value or ""))

    def set_enabled(self, enabled: bool) -> None:
        for key, widget in self.widgets.items():
            if isinstance(widget, tk.Text):
                widget.configure(state="normal" if enabled else "disabled")
            elif isinstance(widget, ttk.Combobox):
                widget.configure(state=("readonly" if key in self._choices else "normal") if enabled else "disabled")
            elif isinstance(widget, (ttk.Entry, ttk.Checkbutton)):
                widget.state(["!disabled"] if enabled else ["disabled"])

    def get_number(self, key: str, errors: list[str], integer: bool = False) -> float | int | None:
        """Parse a numeric field; on failure append a message to `errors` and return None."""
        raw = self.get(key)
        try:
            value = parse_number(raw)
        except ValueError:
            errors.append(f"«{self._labels[key]}»: «{raw}» er ikke et tall.")
            return None
        if value is not None and integer:
            if not value.is_integer():
                errors.append(f"«{self._labels[key]}»: «{raw}» må være et heltall.")
                return None
            return int(value)
        return value


class EditDialog(tk.Toplevel):
    """Modal dialog with a Form and Lagre/Avbryt buttons.

    Subclasses fill self.form and implement save(), which raises
    ValidationError to keep the dialog open with an error message.
    """

    def __init__(self, master: tk.Misc, title: str, columns: int = 1):
        super().__init__(master)
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.saved = False
        self.form = Form(self, columns=columns, padding=12)
        self.form.pack(fill="both", expand=True)
        self.extra = ttk.Frame(self, padding=(12, 0, 12, 8))  # for widgets that don't fit the form grid
        self.extra.pack(fill="both", expand=True)
        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Avbryt", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Lagre", command=self._on_save).pack(side="right", padx=6)
        self.bind("<Escape>", lambda _e: self.destroy())
        top = master.winfo_toplevel()
        self.geometry(f"+{top.winfo_rootx() + 120}+{top.winfo_rooty() + 80}")

    def show(self) -> bool:
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.saved

    def _on_save(self) -> None:
        try:
            if self.save() is False:  # the user cancelled a confirmation
                return
        except ValidationError as exc:
            show_errors(self, exc.errors)
            return
        self.saved = True
        self.destroy()

    def save(self) -> bool | None:
        raise NotImplementedError


class Checklist(ttk.Frame):
    """Check boxes for picking several items (ids), with «Velg alle» / «Velg ingen».

    Scrolls when there are more items than fit in `max_height` pixels.
    """

    def __init__(self, master: tk.Misc, items: Sequence[tuple[int, str]], selected: Iterable[int] = (),
                 max_height: int = 150):
        super().__init__(master)
        selected = set(selected)
        self.vars: dict[int, tk.BooleanVar] = {}
        background = ttk.Style(self).lookup("TFrame", "background") or None
        canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=background)
        inner = ttk.Frame(canvas)
        for row, (item_id, text) in enumerate(items):
            var = self.vars[item_id] = tk.BooleanVar(value=item_id in selected)
            ttk.Checkbutton(inner, text=text, variable=var).grid(row=row, column=0, sticky="w", pady=1)
        canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.update_idletasks()
        height = min(inner.winfo_reqheight(), max_height)
        canvas.configure(width=inner.winfo_reqwidth(), height=max(height, 1),
                         scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight()))
        canvas.grid(row=0, column=0, sticky="nsew")
        if inner.winfo_reqheight() > max_height:
            ys = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=ys.set)
            ys.grid(row=0, column=1, sticky="ns")
        if not items:
            ttk.Label(self, text="Ingen skifter registrert.").grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(buttons, text="Velg alle", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Velg ingen", command=lambda: self.set_all(False)).pack(side="left", padx=(6, 0))

    def selected(self) -> list[int]:
        return [item_id for item_id, var in self.vars.items() if var.get()]

    def set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)
