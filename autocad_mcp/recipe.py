"""Recipes: the ordered steps that turn a source into a shop drawing, saved as YAML in
<source folder>/recipe/<name>.yaml.

Status moves draft -> tested -> approved. Saving a recipe always sets it back to draft,
and approval locks the hash of every routine it uses.

Step types (one per step; "answers" and "note" are optional on any step):
    - lisp: MYCMD                 run a LISP command
      file: mycmd.lsp             from the trusted LISP folder
      answers: ["_ALL", ""]       replies to its prompts, in order ("" is Enter)
    - command: "-PURGE"           run an AutoCAD command
      answers: ["_ALL", "*", "_N"]
    - rename_layer: {from: OLD, to: NEW}         merges into NEW if it exists
    - delete: {layer: "TEMP*", type: TEXT}       a selector: layer / type / block
    - change_layer: {select: {type: DIMENSION}, to: DIM}
    - lisp_call: GTAGA-core       call a LISP function directly (no prompts)
      files: [gtag.lsp]           loaded first, in order ("file" for one)
      args: ["{outfall_mh}"]      "{name}" = a run parameter, given when the recipe runs

Run parameters keep job values (names, levels, coordinates) out of the recipe itself, so the
same recipe runs on any source. Every save is also kept in recipe/history/, with who wrote it
(the model or the user), so a model's draft and the user's corrected version stay side by side.
"""
import os
import re
import shutil

import yaml

import job
from backends.selection import FIELDS

STEP_TYPES = ("lisp", "lisp_call", "command", "rename_layer", "delete", "change_layer")
STATUSES = ("draft", "tested", "approved")
NAME_RE = r"[A-Za-z0-9_\-]+"
FUNC_RE = r"[A-Za-z0-9_\-:]+"
PARAM_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
AUTHORS = ("model", "user")


def path(source_folder: str, name: str) -> str:
    if not re.fullmatch(NAME_RE, name):
        raise ValueError(f"recipe name may use letters, digits, _ and - only: {name!r}")
    return os.path.join(job.sub(source_folder, "recipe"), f"{name}.yaml")


def step_type(step: dict) -> str:
    return next(t for t in STEP_TYPES if t in step)


def _selector_errors(sel, where: str) -> list[str]:
    if not isinstance(sel, dict) or not sel:
        return [f"{where}: needs a selector with any of {list(FIELDS)}"]
    unknown = set(sel) - set(FIELDS)
    return [f"{where}: unknown selector fields {sorted(unknown)}"] if unknown else []


def validate(steps) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return ["a recipe needs at least one step"]
    errors = []
    for i, s in enumerate(steps, 1):
        where = f"step {i}"
        if not isinstance(s, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        types = [t for t in STEP_TYPES if t in s]
        if len(types) != 1:
            errors.append(f"{where}: needs exactly one of {list(STEP_TYPES)}")
            continue
        t = types[0]
        allowed = {t, "answers", "note"}
        if t in ("lisp", "lisp_call"):
            allowed |= {"file", "files"} | ({"args"} if t == "lisp_call" else set())
        if set(s) - allowed:
            errors.append(f"{where}: unexpected keys {sorted(set(s) - allowed)}")
        answers = s.get("answers", [])
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            errors.append(f"{where}: answers must be a list of strings")
        if t in ("lisp", "lisp_call"):
            if "file" in s and "files" in s:
                errors.append(f"{where}: give file or files, not both")
            files = step_files(s)
            if not files or not all(isinstance(f, str) and f for f in files):
                errors.append(f"{where}: {t} steps need the routine file(s) to load")
        if t == "lisp":
            if not isinstance(s["lisp"], str) or not re.fullmatch(NAME_RE, s["lisp"]):
                errors.append(f"{where}: lisp must be a command name (without c:)")
        elif t == "lisp_call":
            if not isinstance(s["lisp_call"], str) or not re.fullmatch(FUNC_RE, s["lisp_call"]):
                errors.append(f"{where}: lisp_call must be a function name")
            args = s.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, (str, int, float)) for a in args):
                errors.append(f"{where}: args must be a list of text or numbers")
        elif t == "command":
            if not isinstance(s["command"], str) or not s["command"].strip():
                errors.append(f"{where}: command must be text")
        elif t == "rename_layer":
            v = s["rename_layer"]
            if not isinstance(v, dict) or set(v) != {"from", "to"}:
                errors.append(f"{where}: rename_layer needs from and to")
        elif t == "delete":
            errors += _selector_errors(s["delete"], where)
        elif t == "change_layer":
            v = s["change_layer"]
            if not isinstance(v, dict) or set(v) != {"select", "to"}:
                errors.append(f"{where}: change_layer needs select and to")
            else:
                errors += _selector_errors(v["select"], where)
    return errors


def load(source_folder: str, name: str) -> dict:
    p = path(source_folder, name)
    if not os.path.exists(p):
        raise FileNotFoundError(f"no recipe {name!r} in {os.path.dirname(p)}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save(source_folder: str, name: str, recipe: dict, event: str = "saved") -> str:
    """Write the recipe, and keep a copy of this version in recipe/history/."""
    p = path(source_folder, name)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(recipe, f, sort_keys=False, allow_unicode=True)
    history = os.path.join(os.path.dirname(p), "history")
    os.makedirs(history, exist_ok=True)
    who = recipe.get("author", "user")
    shutil.copy2(p, os.path.join(history, f"{name}-{job.stamp()}-{event}-{who}.yaml"))
    return p


def history(source_folder: str, name: str) -> list[str]:
    """Saved versions of a recipe, oldest first (file names in recipe/history/)."""
    folder = os.path.join(os.path.dirname(path(source_folder, name)), "history")
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if f.startswith(f"{name}-") and f.endswith(".yaml"))


def step_files(step: dict) -> list:
    """The routine files a lisp or lisp_call step loads, in order."""
    if "files" in step:
        return step["files"] if isinstance(step["files"], list) else []
    return [step["file"]] if "file" in step else []


def params(recipe: dict) -> list[str]:
    """Run parameter names ("{name}") used in the recipe's answers and args."""
    found = set()
    for s in recipe["steps"]:
        for v in [*s.get("answers", []), *s.get("args", [])]:
            if isinstance(v, str):
                found |= set(PARAM_RE.findall(v))
    return sorted(found)


def fill(value, values: dict):
    """Put run parameter values into "{name}" placeholders."""
    if not isinstance(value, str):
        return value
    missing = [p for p in PARAM_RE.findall(value) if p not in values]
    if missing:
        raise ValueError(f"run parameter(s) not given: {missing}")
    return PARAM_RE.sub(lambda m: str(values[m.group(1)]), value)


def routines(recipe: dict) -> list[str]:
    return sorted({f for s in recipe["steps"] if "lisp" in s or "lisp_call" in s for f in step_files(s)})


def current_hashes(recipe: dict) -> dict:
    """Routine file -> hash of the version now in the trusted folder (None if missing, or if
    the trusted folder is not set yet)."""
    out = {}
    for f in routines(recipe):
        try:
            out[f] = job.file_hash(job.routine_path(f))
        except (FileNotFoundError, RuntimeError):
            out[f] = None
    return out


def hash_changes(recipe: dict) -> list[str]:
    """Routines that differ from the versions locked at approval."""
    approved = (recipe.get("approved") or {}).get("hashes")
    if not approved:
        return []
    now = current_hashes(recipe)
    return [f for f in sorted(set(approved) | set(now)) if approved.get(f) != now.get(f)]
