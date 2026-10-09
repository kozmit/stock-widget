"""Testläufe in drei Stufen, damit nicht bei jeder kleinen Änderung die ganze Suite läuft.

  python run_tests.py              schnelle Stufe: alles außer tests/test_ui.py (wenige Sekunden)
  python run_tests.py MainWindow   zusätzlich nur die UI-Testklassen, deren Name den Text enthält
  python run_tests.py all          die ganze Suite, parallel auf mehrere Prozesse verteilt (vor dem Push)
"""
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
UI = "tests.test_ui"
WORKERS = min(8, os.cpu_count() or 2)


def collect(module):
    """Testklassen eines Moduls als Liste von 'modul.Klasse'."""
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    names = []

    def walk(item):
        if isinstance(item, unittest.TestSuite):
            for sub in item:
                walk(sub)
        else:
            name = f"{type(item).__module__}.{type(item).__name__}"
            if name not in names:
                names.append(name)
    walk(suite)
    return names


def fast_modules():
    tests = os.path.join(ROOT, "tests")
    modules = sorted(f[:-3] for f in os.listdir(tests) if f.startswith("test_") and f.endswith(".py"))
    return [f"tests.{m}" for m in modules if f"tests.{m}" != UI]


def run(targets):
    """Ein unittest-Prozess; gibt (Ziele, Exitcode, Ausgabe) zurück."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-m", "unittest", *targets], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    return targets, result.returncode, result.stdout + result.stderr


def main(argv):
    if argv == ["all"]:
        # test_ui ist der Großteil der Zeit, deshalb nach Klassen aufteilen; der Rest läuft als ein Block
        jobs = [fast_modules()] + [[name] for name in collect(UI)]
    elif argv:
        wanted = [n for n in collect(UI) if any(a.lower() in n.lower() for a in argv)]
        if not wanted:
            print(f"Keine UI-Testklasse passt zu {argv}. Vorhanden: {', '.join(n.split('.')[-1] for n in collect(UI))}")
            return 2
        jobs = [fast_modules(), wanted]
    else:
        jobs = [fast_modules()]

    failed = False
    with ThreadPoolExecutor(WORKERS) as pool:
        for targets, code, output in pool.map(run, jobs):
            if code:
                failed = True
                print(f"FEHLGESCHLAGEN: {' '.join(targets)}\n{output}")
    print("ALLE TESTS OK" if not failed else "TESTS FEHLGESCHLAGEN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
