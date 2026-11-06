"""Getting an embedding model to work without thinking about interpreters. `kb_rag.py setup` builds a private
virtualenv in the data directory and remembers its python; from then on every run of the engine, from a skill's
`python3` or from the hook, continues in that python. System pythons refuse `pip install` (PEP 668), and a model
in a virtualenv nobody calls is no model at all.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

from .core import al_home

PREFERRED = ("python3.13", "python3.12", "python3.11", "python3.10")  # those with onnxruntime wheels; newest first
LAST_RESORT = ("python3.14", "python3")
DOWNLOAD_TIMEOUT = 1800


def _pointer() -> pathlib.Path:
    return al_home() / "model-python"


def remembered_python() -> str:
    try:
        return _pointer().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def continue_in_model_python() -> None:
    """Re-run this command in the python that has the model. Called first thing; never returns if it switches."""
    if os.environ.get("AUTOLEARN_REEXEC"):
        return
    explicit = os.environ.get("AUTOLEARN_PYTHON")
    target = explicit or remembered_python()
    if not target or os.path.abspath(target) == os.path.abspath(sys.executable):
        return
    if not os.path.exists(target):
        source = "AUTOLEARN_PYTHON" if explicit else "the python from `kb_rag.py setup`"
        print(f"! {source} is {target}, which does not exist; continuing with {sys.executable}", file=sys.stderr)
        return
    os.environ["AUTOLEARN_REEXEC"] = "1"
    sys.stdout.flush()
    os.execv(target, [target, *sys.argv])


def _pick_python(wanted: str) -> str:
    if wanted:
        return wanted if shutil.which(wanted) or os.path.exists(wanted) else ""
    for name in PREFERRED:
        found = shutil.which(name)
        if found:
            return found
    if sys.version_info >= (3, 10):
        return sys.executable
    return next((shutil.which(name) for name in LAST_RESORT if shutil.which(name)), "") or sys.executable


def setup(engine: str, wanted: str = "") -> int:
    """Create the virtualenv, install fastembed, remember the python, build the index with the model."""
    python = _pick_python(wanted)
    if not python:
        print(f"! no python found at {wanted}", file=sys.stderr)
        return 1
    venv = al_home() / "venv"
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    print(f"Installing fastembed into {venv} with {python}: about 150 MB of packages, then a 220 MB model.", flush=True)
    steps = [[python, "-m", "venv", str(venv)],
             [str(venv_python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "fastembed"]]
    for step in steps:
        done = subprocess.run(step, capture_output=True, text=True)
        if done.returncode:
            print(f"! {' '.join(step[:4])} failed: {done.stderr[-400:]}", file=sys.stderr)
            print("! try another interpreter: kb_rag.py setup --python python3.12", file=sys.stderr)
            return 1
    _pointer().parent.mkdir(parents=True, exist_ok=True)
    _pointer().write_text(str(venv_python), encoding="utf-8")
    env = dict(os.environ, AUTOLEARN_REEXEC="1")
    env.pop("AUTOLEARN_EMBED", None)
    print("Packages are in. Downloading the model and building the index with it...", flush=True)
    try:
        return subprocess.run([str(venv_python), engine, "index"], env=env, timeout=DOWNLOAD_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        print(f"! no result after {DOWNLOAD_TIMEOUT // 60} minutes. The packages stay installed: run `kb_rag.py index` to "
              "try the model again.", file=sys.stderr)
        return 1
