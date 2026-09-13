"""
DB-Style Zugverbindungs-Widget für den Windows-Desktop
=========================================================

Zeigt die aktuelle Abfahrtszeit deiner Zugverbindung im Look der
Bahn-App an: Abfahrt, Echtzeit, Zug, Gleis, Ziel.

Datenquelle:
- iris.noncd.db.de   -> Echtzeit-Abfahrten (offizielle, kostenlose
  Schnittstelle der DB, dieselbe Quelle wie die Bahnhofstafeln)
- reiseauskunft.bahn.de -> Stationssuche (löst deinen eingegebenen
  Stationsnamen automatisch in die interne EVA-Nummer auf)

INSTALLATION
------------
1. Python 3.10+ (python.org, Haken bei "Add to PATH")
2. Starten mit:  pythonw db_widget.py
3. Rechtsklick -> "Einstellungen": Station, Zugtyp, ggf. Ziel eintragen

BEDIENUNG
---------
- Fenster frei verschiebbar (linke Maustaste halten + ziehen)
- Rechtsklick -> Einstellungen öffnen, manuell aktualisieren oder beenden
- Aktualisiert sich automatisch alle X Sekunden (einstellbar)

Einstellungen werden in db_widget_config.json (gleicher Ordner)
gespeichert und beim nächsten Start automatisch geladen.
"""

import json
import os
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ============================== FARBEN (DB-Stil) ==============================

BG_COLOR = "#181818"
FG_COLOR = "#ffffff"
GREEN = "#38b56a"
RED = "#ec0016"
GREY = "#3a3f4a"
SUBTEXT = "#9a9a9a"

IRIS_BASE = "https://iris.noncd.db.de/iris-tts/timetable"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_widget_config.json")

# Bekannte Fernverkehrs-Bahnhöfe (EVA-Nummern). Für alle anderen Stationen
# einfach direkt die EVA-Nummer eintragen (Ziffern), zu finden über die
# URL auf https://www.bahnhof.de bei der jeweiligen Station.
KNOWN_STATIONS = {
    "berlin hbf": "8011160",
    "berlin südkreuz": "8011113",
    "berlin ostbahnhof": "8010255",
    "berlin gesundbrunnen": "8011102",
    "hamburg hbf": "8002549",
    "hamburg-altona": "8002548",
    "münchen hbf": "8000261",
    "frankfurt(main)hbf": "8000105",
    "frankfurt hbf": "8000105",
    "köln hbf": "8000207",
    "düsseldorf hbf": "8000085",
    "stuttgart hbf": "8000096",
    "hannover hbf": "8000152",
    "leipzig hbf": "8010205",
    "dresden hbf": "8010085",
    "nürnberg hbf": "8000284",
    "dortmund hbf": "8000080",
    "essen hbf": "8000098",
    "bremen hbf": "8000050",
    "mannheim hbf": "8000244",
    "halle(saale)hbf": "8010159",
    "halle saale hbf": "8010159",
}

DEFAULT_CONFIG = {
    "station_query": "Düsseldorf Hbf",   # was der Nutzer eingibt
    "station_name": "Düsseldorf Hbf",    # aufgelöster Anzeigename
    "eva_id": "8000085",                 # aufgelöste EVA-Nummer (automatisch)
    "train_type": "",                    # z.B. "IC", "ICE", "RE" - exakter Abgleich, leer = egal
    "destination_filter": "",            # optional, Teil des Zielnamens, leer = egal
    "line_filter": "",                   # optional, konkrete Zugnummer
    "refresh_seconds": 60,
}

# =================================================================================


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def http_get(url: str, retries: int = 3, timeout: int = 20) -> bytes:
    last_error = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "db-widget/4.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            time.sleep(2)
    raise last_error


def resolve_station(query: str) -> tuple[str, str]:
    """Löst einen Stationsnamen in (eva_id, kanonischer_name) auf.

    Direkte EVA-Nummer (nur Ziffern) wird unverändert übernommen.
    Ansonsten wird in der eingebauten Liste bekannter Fernverkehrs-
    Bahnhöfe gesucht (exakt, dann als Teilstring).
    """
    q = query.strip()
    if q.isdigit():
        return q, query  # direkte EVA-Eingabe, Anzeigename bleibt wie eingegeben

    q_lower = q.lower()
    if q_lower in KNOWN_STATIONS:
        return KNOWN_STATIONS[q_lower], query

    matches = {k: v for k, v in KNOWN_STATIONS.items() if q_lower in k}
    if len(matches) == 1:
        (name, eva), = matches.items()
        return eva, query
    if len(matches) > 1:
        suggestions = ", ".join(sorted(matches))
        raise ValueError(f"Mehrdeutig, meinst du: {suggestions}?")

    raise ValueError(
        "Unbekannte Station. Bitte die EVA-Nummer direkt eintragen "
        "(Ziffern, zu finden über die URL auf bahnhof.de)"
    )


def parse_dbtime(s: str) -> datetime:
    return datetime.strptime(s, "%y%m%d%H%M")


def fetch_departures(eva_id: str) -> list[dict]:
    """Holt Fahrplan (plan) + Echtzeit-Änderungen (fchg) und führt sie zusammen."""
    now = datetime.now()
    stops = {}

    for delta in (0, 1, 2):
        t = now + timedelta(hours=delta)
        url = f"{IRIS_BASE}/plan/{eva_id}/{t:%y%m%d}/{t:%H}"
        try:
            xml_bytes = http_get(url)
        except Exception:
            continue
        root = ET.fromstring(xml_bytes)
        for s in root.findall("s"):
            sid = s.get("id")
            tl = s.find("tl")
            dp = s.find("dp")
            if tl is None or dp is None or dp.get("pt") is None:
                continue
            stops[sid] = {
                "category": tl.get("c", ""),
                "number": tl.get("n", ""),
                "planned": parse_dbtime(dp.get("pt")),
                "actual": None,
                "platform": dp.get("pp", "?"),
                "path": dp.get("ppth", ""),
            }

    try:
        fchg_bytes = http_get(f"{IRIS_BASE}/fchg/{eva_id}")
        root = ET.fromstring(fchg_bytes)
        for s in root.findall("s"):
            sid = s.get("id")
            if sid not in stops:
                continue
            dp = s.find("dp")
            if dp is None:
                continue
            if dp.get("ct"):
                stops[sid]["actual"] = parse_dbtime(dp.get("ct"))
            if dp.get("cp"):
                stops[sid]["platform"] = dp.get("cp")
    except Exception:
        pass

    return list(stops.values())


def find_next_departure(cfg: dict) -> dict:
    now = datetime.now() - timedelta(minutes=2)
    candidates = []
    for dep in fetch_departures(cfg["eva_id"]):
        if cfg["train_type"] and dep["category"].lower() != cfg["train_type"].lower():
            continue
        if cfg["destination_filter"] and cfg["destination_filter"].lower() not in dep["path"].lower():
            continue
        if cfg["line_filter"] and cfg["line_filter"].strip() != dep["number"].strip():
            continue
        effective = dep["actual"] or dep["planned"]
        if effective >= now:
            candidates.append(dep)
    if not candidates:
        return {}
    candidates.sort(key=lambda d: d["actual"] or d["planned"])
    return candidates[0]


class SettingsWindow(tk.Toplevel):
    def __init__(self, master, cfg: dict, on_save):
        super().__init__(master)
        self.title("Einstellungen")
        self.configure(bg=BG_COLOR)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.on_save = on_save
        self.cfg = cfg

        fields = [
            ("station_query", "Station (Name)"),
            ("train_type", "Zugtyp (optional, z.B. IC, ICE, RE)"),
            ("destination_filter", "Ziel (optional, Teil des Namens)"),
            ("line_filter", "Zugnummer (optional, z.B. 60402)"),
            ("refresh_seconds", "Aktualisierung (Sekunden)"),
        ]

        self.vars = {}
        for row, (key, label) in enumerate(fields):
            tk.Label(
                self, text=label, fg=FG_COLOR, bg=BG_COLOR, font=("Segoe UI", 10)
            ).grid(row=row, column=0, sticky="w", padx=12, pady=(12 if row == 0 else 4, 4))
            var = tk.StringVar(value=str(cfg.get(key, "")))
            entry = tk.Entry(
                self, textvariable=var, width=28, font=("Segoe UI", 10),
                bg="#2a2a2a", fg=FG_COLOR, insertbackground=FG_COLOR, relief="flat"
            )
            entry.grid(row=row, column=1, padx=(4, 12), pady=(12 if row == 0 else 4, 4))
            self.vars[key] = var

        self.lbl_status = tk.Label(
            self, text="", fg=SUBTEXT, bg=BG_COLOR, font=("Segoe UI", 9)
        )
        self.lbl_status.grid(row=len(fields), column=0, columnspan=2, sticky="w", padx=12)

        btn_frame = tk.Frame(self, bg=BG_COLOR)
        btn_frame.grid(row=len(fields) + 1, column=0, columnspan=2, pady=14)

        self.btn_save = tk.Button(
            btn_frame, text="Speichern", command=self._save,
            bg=RED, fg=FG_COLOR, relief="flat", padx=14, pady=4,
            font=("Segoe UI", 10, "bold")
        )
        self.btn_save.pack(side="left", padx=6)

        tk.Button(
            btn_frame, text="Abbrechen", command=self.destroy,
            bg=GREY, fg=FG_COLOR, relief="flat", padx=14, pady=4,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=6)

    def _save(self):
        try:
            refresh = int(self.vars["refresh_seconds"].get())
            if refresh < 10:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Ungültiger Wert",
                "Aktualisierung muss eine Zahl (mindestens 10 Sekunden) sein.",
                parent=self,
            )
            return

        station_query = self.vars["station_query"].get().strip()
        if not station_query:
            messagebox.showerror("Fehlende Angabe", "Bitte eine Station eingeben.", parent=self)
            return

        self.btn_save.config(state="disabled", text="Suche Station …")
        self.lbl_status.config(text="Löse Stationsnamen auf …")
        threading.Thread(target=self._resolve_and_save, args=(station_query, refresh), daemon=True).start()

    def _resolve_and_save(self, station_query: str, refresh: int):
        try:
            eva, canonical_name = resolve_station(station_query)
            error = None
        except Exception as e:
            eva, canonical_name, error = None, None, str(e)
        self.after(0, self._finish_save, station_query, refresh, eva, canonical_name, error)

    def _finish_save(self, station_query, refresh, eva, canonical_name, error):
        self.btn_save.config(state="normal", text="Speichern")
        if error or not eva:
            self.lbl_status.config(text=f"Station nicht gefunden: {error or 'unbekannter Fehler'}")
            return

        new_cfg = {
            "station_query": station_query,
            "station_name": canonical_name,
            "eva_id": eva,
            "train_type": self.vars["train_type"].get().strip(),
            "destination_filter": self.vars["destination_filter"].get().strip(),
            "line_filter": self.vars["line_filter"].get().strip(),
            "refresh_seconds": refresh,
        }
        self.on_save(new_cfg)
        self.destroy()


class DBWidget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG_COLOR)
        self.geometry("+80+80")

        self._offset_x = 0
        self._offset_y = 0
        self.bind("<ButtonPress-1>", self._start_move)
        self.bind("<B1-Motion>", self._do_move)
        self.bind("<Button-3>", self._show_context_menu)

        self._build_ui()
        self._build_context_menu()
        self._stop_event = threading.Event()
        self._start_refresh_loop()

    # ---- UI ----
    def _build_ui(self):
        pad = 18
        self.frame = tk.Frame(self, bg=BG_COLOR, padx=pad, pady=pad)
        self.frame.pack()

        self.lbl_planned = tk.Label(
            self.frame, text="--:--", font=("Segoe UI", 22, "bold"),
            fg=FG_COLOR, bg=BG_COLOR
        )
        self.lbl_planned.grid(row=0, column=0, sticky="w")

        self.lbl_station = tk.Label(
            self.frame, text=self.cfg["station_name"], font=("Segoe UI", 13, "bold"),
            fg=FG_COLOR, bg=BG_COLOR
        )
        self.lbl_station.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.lbl_realtime = tk.Label(
            self.frame, text="", font=("Segoe UI", 11, "bold"),
            fg=GREEN, bg=BG_COLOR
        )
        self.lbl_realtime.grid(row=1, column=0, sticky="w")

        self.lbl_line = tk.Label(
            self.frame, text="", font=("Segoe UI", 11, "bold"),
            fg=FG_COLOR, bg=GREY, padx=8, pady=3
        )
        self.lbl_line.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 14))

        self.lbl_destination = tk.Label(
            self.frame, text=self._destination_label(), font=("Segoe UI", 13, "bold"),
            fg=FG_COLOR, bg=BG_COLOR
        )
        self.lbl_destination.grid(row=3, column=1, sticky="w", padx=(12, 0))

        self.lbl_status = tk.Label(
            self.frame, text="wird geladen …", font=("Segoe UI", 9),
            fg=SUBTEXT, bg=BG_COLOR
        )
        self.lbl_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _destination_label(self) -> str:
        if self.cfg["destination_filter"]:
            return f"→ {self.cfg['destination_filter']}"
        if self.cfg["train_type"]:
            return f"nächster {self.cfg['train_type']}"
        return "nächste Abfahrt"

    def _build_context_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg="#2a2a2a", fg=FG_COLOR)
        self.menu.add_command(label="Einstellungen", command=self._open_settings)
        self.menu.add_command(label="Jetzt aktualisieren", command=self._manual_refresh)
        self.menu.add_separator()
        self.menu.add_command(label="Beenden", command=self._quit)

    def _show_context_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _open_settings(self):
        SettingsWindow(self, self.cfg, self._apply_new_config)

    def _apply_new_config(self, new_cfg: dict):
        self.cfg = new_cfg
        save_config(self.cfg)
        self.lbl_station.config(text=self.cfg["station_name"])
        self.lbl_destination.config(text=self._destination_label())
        self._manual_refresh()

    def _manual_refresh(self):
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _quit(self):
        self._stop_event.set()
        self.destroy()

    # ---- Fenster verschieben ----
    def _start_move(self, event):
        self._offset_x, self._offset_y = event.x, event.y

    def _do_move(self, event):
        x = self.winfo_pointerx() - self._offset_x
        y = self.winfo_pointery() - self._offset_y
        self.geometry(f"+{x}+{y}")

    # ---- Daten holen & anzeigen ----
    def _start_refresh_loop(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        while not self._stop_event.is_set():
            self._refresh_once()
            self._stop_event.wait(self.cfg["refresh_seconds"])

    def _refresh_once(self):
        try:
            dep = find_next_departure(self.cfg)
            self.after(0, self._update_ui, dep, None)
        except Exception as e:
            self.after(0, self._update_ui, {}, str(e))

    def _update_ui(self, dep: dict, error):
        if error or not dep:
            self.lbl_status.config(
                text="Keine passende Abfahrt gefunden" if not error else f"Fehler: {error}"
            )
            return

        planned = dep["planned"].strftime("%H:%M")
        actual = dep["actual"].strftime("%H:%M") if dep["actual"] else None
        delay_min = int((dep["actual"] - dep["planned"]).total_seconds() // 60) if dep["actual"] else 0

        self.lbl_planned.config(text=planned)
        self.lbl_line.config(text=f"🚆  {dep['category']} {dep['number']}   Gl. {dep['platform']}")

        if actual and actual != planned:
            color = RED if delay_min > 0 else GREEN
            sign = f"+{delay_min}" if delay_min > 0 else ""
            self.lbl_realtime.config(text=f"{actual} {sign}".strip(), fg=color)
        else:
            self.lbl_realtime.config(text=planned, fg=GREEN)

        self.lbl_status.config(text=f"aktualisiert {time.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    app = DBWidget()
    app.mainloop()
