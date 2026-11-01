"""The job folder (the source folder holding the source and sample drawings), the trusted
LISP folder, and the record each job keeps of what it used. The tools never write beside the
drawings: everything goes in one work subfolder, created on first use.

    <source folder>/
        source / sample drawings          read only
        AI work/                          (work_subfolder in settings.ini)
            lisp/     copies of the routines this job used, so it can be repeated later
            test/     test copies and test reports
            recipe/   the job's recipes
            shop/     shop drawings and the reports of full runs
            drafts/   the model's own drafts, kept beside the user's corrected versions
            job-summary.json / .md
"""
import hashlib
import json
import os
import re
import shutil
import time

import settings

SUBFOLDERS = ("lisp", "test", "recipe", "shop", "drafts")
LISP_DIR_ENV = "AUTOCAD_MCP_LISP_DIR"


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def inside(path: str, folder: str) -> bool:
    p, f = _norm(path), _norm(folder)
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:  # different drives
        return False


# The trusted LISP folder


def lisp_dir() -> str:
    """AutoCAD's trusted LISP folder, the only place routines are loaded from:
    AUTOCAD_MCP_LISP_DIR if set, otherwise lisp_dir in settings.ini."""
    d = os.environ.get(LISP_DIR_ENV) or settings.get("lisp_dir")
    if not d:
        raise RuntimeError(f"Set lisp_dir in {settings.path()} (or {LISP_DIR_ENV}) "
                           "to AutoCAD's trusted LISP folder")
    d = os.path.abspath(d)
    if not os.path.isdir(d):
        raise RuntimeError(f"{LISP_DIR_ENV} folder does not exist: {d}")
    return d


def routine_path(name: str) -> str:
    """A routine file in the trusted folder. Paths outside it are refused."""
    base = lisp_dir()
    target = os.path.abspath(os.path.join(base, name))
    if not inside(target, base):
        raise ValueError(f"{name} is outside the trusted LISP folder {base}")
    if not os.path.isfile(target):
        raise FileNotFoundError(f"{os.path.relpath(target, base)} is not in the trusted LISP folder {base}")
    return target


def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def defun_names(path: str) -> list[str]:
    """Every function a .lsp file defines, lower case, in order."""
    with open(path, "rb") as f:
        text = f.read().decode("latin-1")
    text = re.sub(r";[^\n]*", "", text)  # drop comments
    names = re.findall(r"\(\s*defun\s+([^\s()]+)", text, re.IGNORECASE)
    return list(dict.fromkeys(n.lower() for n in names))


def defun_clashes(path: str) -> dict:
    """Functions this file defines that another file in the trusted folder also defines.
    Once both are loaded, the one loaded last silently replaces the other."""
    mine = set(defun_names(path))
    clashes = {}
    for root, _, files in os.walk(lisp_dir()):
        for f in files:
            other = os.path.join(root, f)
            if f.lower().endswith(".lsp") and _norm(other) != _norm(path):
                for name in mine & set(defun_names(other)):
                    clashes.setdefault(name, []).append(os.path.relpath(other, lisp_dir()))
    return dict(sorted(clashes.items()))


# The job folder


def folder(source_folder: str) -> str:
    """The job's source folder (holding the source and sample). Nothing is created in it."""
    root = os.path.abspath(source_folder)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"source folder does not exist: {root}")
    return root


def work(source_folder: str, create: bool = True) -> str:
    """The job's work folder, <source folder>/<work_subfolder> ("AI work" by default), with
    its subfolders. Everything the tools write goes here, never beside the drawings."""
    root = os.path.join(folder(source_folder), settings.get("work_subfolder"))
    if create:
        for name in SUBFOLDERS:
            os.makedirs(os.path.join(root, name), exist_ok=True)
    return root


def sub(source_folder: str, name: str, create: bool = True) -> str:
    """A subfolder of the work folder. create=False only builds the path (for listings)."""
    return os.path.join(work(source_folder, create), name)


def resolve(source_folder: str, path: str) -> str:
    """A drawing path. Relative paths are taken from the source folder, except those that
    start with a work subfolder (e.g. "test/x.dwg"), which are taken from the work folder."""
    if os.path.isabs(path):
        return os.path.abspath(path)
    first = path.replace("\\", "/").split("/")[0]
    base = work(source_folder, create=False) if first in SUBFOLDERS else folder(source_folder)
    return os.path.abspath(os.path.join(base, path))


def record_routine(source_folder: str, path: str) -> dict:
    """Copy a routine into <source folder>/lisp/ as the job's record of what ran.
    If an older version is already recorded there, it is kept under a name with its hash."""
    digest = file_hash(path)
    target = os.path.join(sub(source_folder, "lisp"), os.path.basename(path))
    if os.path.exists(target):
        old = file_hash(target)
        if old == digest:
            return {"file": os.path.basename(path), "sha256": digest}
        stem, ext = os.path.splitext(target)
        os.replace(target, f"{stem}.{old[:8]}{ext}")
    shutil.copy2(path, target)
    return {"file": os.path.basename(path), "sha256": digest}


def write_report(source_folder: str, subfolder: str, name: str, data: dict) -> str:
    path = os.path.join(sub(source_folder, subfolder), f"{name}-{stamp()}.json")
    data["report_file"] = path
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    return path


def reports(source_folder: str, subfolder: str, kind: str) -> list[dict]:
    """Saved reports of one kind ("test", "recipe-test", "recipe-full"), oldest first."""
    out = []
    folder_path = sub(source_folder, subfolder)
    for f in sorted(os.listdir(folder_path)):
        if f.endswith(".json"):
            with open(os.path.join(folder_path, f), encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("kind") == kind:
                out.append(data)
    return out


def latest_test(source_folder: str, routine: str, command: str) -> dict | None:
    """The latest test_routine result for this routine and command, or None if untested."""
    found = [r for r in reports(source_folder, "test", "test")
             if r["routine"].lower() == routine.lower() and r["command"].upper() == command.upper()]
    if not found:
        return None
    r = found[-1]
    return {"result": r["result"], "flags": r["flags"], "sha256": r["sha256"], "report_file": r["report_file"]}
