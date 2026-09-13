"""
Build-Skript, um db_widget.py mit py2exe in eine eigenständige
Windows-.exe zu verpacken (keine Python-Installation beim Nutzer nötig).

INSTALLATION
------------
pip install py2exe

BUILD
-----
In der Konsole, im selben Ordner wie db_widget.py und diese Datei:

    python setup.py py2exe

Das Ergebnis liegt danach in:  dist\\db_widget.exe

Die exe braucht keine Konsole (windows=..., nicht console=...), lässt
sich also direkt per Doppelklick starten oder in shell:startup für
Autostart verknüpfen.

HINWEIS
-------
py2exe unterstützt aktuelle Python-Versionen (3.12+) nicht immer
zuverlässig. Falls der Build fehlschlägt, ist PyInstaller die robustere
Alternative:

    pip install pyinstaller
    pyinstaller --onefile --windowed --name db_widget db_widget.py

Das erzeugt dist\\db_widget.exe auf die gleiche Art, meist mit
weniger Kompatibilitätsproblemen bei neuen Python-Versionen.
"""

from setuptools import setup
import py2exe  # noqa: F401  (registriert den "py2exe"-Befehl bei setuptools)

setup(
    name="DB Widget",
    version="1.0",
    windows=[
        {
            "script": "db_widget.py",
            "dest_base": "db_widget",
        }
    ],
    options={
        "py2exe": {
            "bundle_files": 1,   # alles in eine exe packen
            "compressed": True,
            "optimize": 2,
        }
    },
    zipfile=None,
)
