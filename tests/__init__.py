import os

# Die Oberflächentests laufen ohne sichtbare Fenster.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
