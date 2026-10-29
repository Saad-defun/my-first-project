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
"""
import os
import re

import yaml

import job
from backends.selection import FIELDS

STEP_TYPES = ("lisp", "command", "rename_layer", "delete", "change_layer")
STATUSES = ("draft", "tested", "approved")
NAME_RE = r"[A-Za-z0-9_\-]+"


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
        allowed = {t, "answers", "note"} | ({"file"} if t == "lisp" else set())
        if set(s) - allowed:
            errors.append(f"{where}: unexpected keys {sorted(set(s) - allowed)}")
        answers = s.get("answers", [])
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            errors.append(f"{where}: answers must be a list of strings")
        if t == "lisp":
            if not isinstance(s["lisp"], str) or not re.fullmatch(NAME_RE, s["lisp"]):
                errors.append(f"{where}: lisp must be a command name (without c:)")
            if not isinstance(s.get("file"), str):
                errors.append(f"{where}: lisp steps need the routine's file name")
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


def save(source_folder: str, name: str, recipe: dict) -> str:
    p = path(source_folder, name)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(recipe, f, sort_keys=False, allow_unicode=True)
    return p


def routines(recipe: dict) -> list[str]:
    return sorted({s["file"] for s in recipe["steps"] if "lisp" in s})


def current_hashes(recipe: dict) -> dict:
    """Routine file -> hash of the version now in the trusted folder (None if missing)."""
    out = {}
    for f in routines(recipe):
        try:
            out[f] = job.file_hash(job.routine_path(f))
        except FileNotFoundError:
            out[f] = None
    return out


def hash_changes(recipe: dict) -> list[str]:
    """Routines that differ from the versions locked at approval."""
    approved = (recipe.get("approved") or {}).get("hashes")
    if not approved:
        return []
    now = current_hashes(recipe)
    return [f for f in sorted(set(approved) | set(now)) if approved.get(f) != now.get(f)]
