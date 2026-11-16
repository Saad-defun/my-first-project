"""Teaching: how the model learns a task from the user without the user explaining it.

The user does the task once by hand with the TCAP capture routine running (TCAP.lsp in the
trusted LISP folder, from Civil Automation 3.00). TCAP writes a recording folder beside the
drawing: numbered copies of the drawing, one per save, and capture-log.txt with one record
per line, fields separated by ";":

    HEADER;time;drawing path;product;version;user;folder path
    CMD;time;name;STARTED/ENDED/CANCELLED/FAILED;parent
    SEL;time;command or none;GRIP/SELECT;handle;layer;type;block;text
    CHG;time;command or none;ADD/MOD/DEL;handle;layer;type;block;text
    SAVE;time;command;saved file path;copy name
    ERROR;time;where;message
    FOOTER;time;STOP/CLOSE;copies;SEL objects;CHG objects;commands

read_recording() turns that into steps, one per save: the commands run, the objects the
user selected and the objects each command changed. The model reconstructs the task from
the steps, writes the task page, and asks the user only about what the evidence leaves open.

What is learned is kept in files, not in the chat, so it survives the session:

    <teach_dir>/            shared by every job (settings: teach_dir)
        glossary.md         the user's terms (HC, chain, detail...) and how to recognise them
        standards.md        rules that hold on every job
    <job>/<work>/teach/     this job only
        task.md             the task page: what it is, touches, leaves alone, checks
        rules.md            rules learned from the user's corrections on this job
        history/            every earlier version, with who wrote it
"""
import os
import re
import shutil
from collections import Counter

import job
import settings

KINDS = {"task": "job", "rules": "job", "glossary": "global", "standards": "global"}
TEACH_DIR_ENV = "AUTOCAD_MCP_TEACH_DIR"
LOG_NAME = "capture-log.txt"
CAPTURE_ACTIONS = {"start": "TCAP-core-start", "status": "TCAP-core-status", "stop": "TCAP-core-stop"}
MORE_RE = re.compile(r"^\+(\d+) more$")
OBJECT_FIELDS = ("time", "command", "how", "handle", "layer", "type", "block", "text")
RECORD_FIELDS = {
    "HEADER": ("time", "drawing", "product", "version", "user", "folder"),
    "CMD": ("time", "name", "result", "parent"),
    "SEL": OBJECT_FIELDS,
    "CHG": OBJECT_FIELDS,
    "SAVE": ("time", "command", "saved_file", "copy"),
    "ERROR": ("time", "where", "message"),
    "FOOTER": ("time", "reason", "copies", "sel_objects", "chg_objects", "commands"),
}


# The capture log


def parse_log(text: str) -> list[dict]:
    """One dict per log line: {"kind": ..., field: value...}. Unknown lines are kept as
    {"kind": "?", "line": ...}; a "+N more" object line becomes {"more": N}."""
    records = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(";")
        kind = parts[0].upper()
        fields = RECORD_FIELDS.get(kind)
        if not fields:
            records.append({"kind": "?", "line": line})
            continue
        rec = {"kind": kind}
        values = parts[1:]
        for i, name in enumerate(fields):
            rec[name] = values[i] if i < len(values) else ""
        if kind in ("SEL", "CHG"):
            m = next((MORE_RE.match(v) for v in values if MORE_RE.match(v)), None)
            if m:
                rec["more"] = int(m.group(1))
        records.append(rec)
    return records


def _object(rec: dict) -> dict:
    out = {k: rec[k] for k in ("handle", "layer", "type") if rec.get(k)}
    for k in ("block", "text"):
        if rec.get(k):
            out[k] = rec[k]
    return out


def _step(index: int) -> dict:
    return {"step": index, "copy": None, "saved_file": None, "from": None, "to": None,
            "commands": [], "selected": [], "selected_more": 0,
            "changed": {"ADD": [], "MOD": [], "DEL": []}, "changed_more": 0, "errors": []}


def steps_from(records: list[dict]) -> list[dict]:
    """Split the log at each SAVE: step 1 is everything up to the first copy, and so on. A
    final step without a copy holds what happened after the last save."""
    steps, current = [], _step(1)
    open_commands = {}
    saving = None  # the command whose SAVE line was seen; its end closes the step

    def close():
        nonlocal current, saving
        steps.append(current)
        current = _step(len(steps) + 1)
        saving = None

    for rec in records:
        kind = rec["kind"]
        if kind in ("HEADER", "FOOTER", "?"):
            continue
        ends_save = kind == "CMD" and saving is not None and rec["name"] == saving             and rec["result"].upper() != "STARTED"
        if saving is not None and not ends_save:
            close()  # the save's command did not report its end before the next event
        if current["from"] is None:
            current["from"] = rec.get("time")
        current["to"] = rec.get("time")
        if kind == "CMD":
            name, result = rec["name"], rec["result"].upper()
            if result == "STARTED":
                open_commands[name] = rec.get("time")
                continue
            open_commands.pop(name, None)
            current["commands"].append({"name": name, "result": result.lower(),
                                        **({"parent": rec["parent"]} if rec.get("parent") else {})})
            if ends_save:
                close()
        elif kind == "SEL":
            if "more" in rec:
                current["selected_more"] += rec["more"]
            else:
                current["selected"].append({**_object(rec), "command": rec["command"], "how": rec["how"]})
        elif kind == "CHG":
            if "more" in rec:
                current["changed_more"] += rec["more"]
            else:
                how = rec["how"].upper()
                current["changed"].setdefault(how, []).append({**_object(rec), "command": rec["command"]})
        elif kind == "ERROR":
            current["errors"].append({"where": rec["where"], "message": rec["message"]})
        elif kind == "SAVE":
            current["copy"] = rec["copy"]
            current["saved_file"] = rec["saved_file"]
            saving = rec["command"] or ""
    if saving is not None:
        close()
    for name in open_commands:
        current["commands"].append({"name": name, "result": "unfinished"})
    if current["from"] is not None:
        steps.append(current)
    return steps


def _cut(items: list, limit: int) -> list:
    return items[:limit]


def summarise(steps: list[dict], limit: int) -> list[dict]:
    """Steps with exact counts and the object lists cut at limit."""
    out = []
    for s in steps:
        changed_counts = {how: len(v) for how, v in s["changed"].items()}
        out.append({
            "step": s["step"], "copy": s["copy"], "from": s["from"], "to": s["to"],
            "commands": [c["name"] + ("" if c["result"] == "ended" else f" ({c['result']})")
                         for c in s["commands"]],
            "selected": len(s["selected"]) + s["selected_more"],
            "changed": changed_counts,
            "layers_changed": dict(sorted(Counter(o["layer"] for v in s["changed"].values()
                                                  for o in v if o.get("layer")).items())),
            "errors": len(s["errors"]),
            **({"selected_objects": _cut(s["selected"], limit)} if s["selected"] else {}),
            **({"changed_objects": {how: _cut(v, limit) for how, v in s["changed"].items() if v}}
               if any(s["changed"].values()) else {}),
            **({"error_messages": s["errors"]} if s["errors"] else {}),
        })
    return out


def recording_folder(source_folder: str | None, folder: str) -> str:
    """The recording folder: an absolute path, or one relative to the job's source folder."""
    if os.path.isabs(folder):
        return os.path.abspath(folder)
    if not source_folder:
        raise ValueError("give the recording folder as an absolute path, or with source_folder")
    return os.path.join(job.folder(source_folder), folder)


def read_recording(source_folder: str | None, folder: str = "recording",
                   step: int | None = None, limit: int = 50) -> dict:
    """What a TCAP recording holds: the header, the copies found, and the steps (one per
    save). With step, that one step in full; otherwise every step with its lists cut at
    limit (exact counts are always given)."""
    where = recording_folder(source_folder, folder)
    log_path = os.path.join(where, LOG_NAME)
    if not os.path.isfile(log_path):
        raise FileNotFoundError(f"no {LOG_NAME} in {where}")
    with open(log_path, "rb") as f:
        records = parse_log(f.read().decode("latin-1"))
    header = next((r for r in records if r["kind"] == "HEADER"), None)
    footer = next((r for r in records if r["kind"] == "FOOTER"), None)
    copies = sorted(f for f in os.listdir(where)
                    if re.fullmatch(r"\d{3,}\.(dwg|dxf)", f, re.IGNORECASE))
    all_steps = steps_from(records)
    commands = Counter(c["name"] for s in all_steps for c in s["commands"])
    out = {
        "folder": where,
        "header": {k: v for k, v in (header or {}).items() if k != "kind"} or None,
        "footer": {k: v for k, v in (footer or {}).items() if k != "kind"} or None,
        "copies": copies,
        "lines": len(records),
        "unknown_lines": sum(1 for r in records if r["kind"] == "?"),
        "steps_total": len(all_steps),
        "commands_used": dict(commands.most_common()),
        "layers_changed": dict(sorted(Counter(
            o["layer"] for s in all_steps for v in s["changed"].values() for o in v if o.get("layer")).items())),
    }
    if step is not None:
        if not 1 <= step <= len(all_steps):
            raise ValueError(f"step must be 1 to {len(all_steps)}")
        s = all_steps[step - 1]
        out["step"] = {**s, "selected_total": len(s["selected"]) + s["selected_more"],
                       "changed_total": {how: len(v) for how, v in s["changed"].items()}}
    else:
        out["steps"] = summarise(all_steps, limit)
    return out


# Teaching files


def global_dir(create: bool = True) -> str:
    d = os.environ.get(TEACH_DIR_ENV) or settings.get("teach_dir") or os.path.join(
        os.path.dirname(settings.path()), "teaching")
    d = os.path.abspath(d)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def file_path(kind: str, source_folder: str | None, create: bool = True) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    if KINDS[kind] == "global":
        return os.path.join(global_dir(create), f"{kind}.md")
    if not source_folder:
        raise ValueError(f"{kind} belongs to a job: give source_folder")
    return os.path.join(job.sub(source_folder, "teach", create), f"{kind}.md")


def _read(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def read(source_folder: str | None = None) -> dict:
    """Every teaching file, missing ones as null: the shared glossary and standards, and
    the job's task page and rules when a source folder is given."""
    out = {"shared_folder": global_dir(create=False)}
    for kind, scope in KINDS.items():
        if scope == "job" and not source_folder:
            continue
        p = file_path(kind, source_folder, create=False)
        out[kind] = _read(p)
    if source_folder:
        out["job_folder"] = job.sub(source_folder, "teach", create=False)
    return out


def write(kind: str, text: str, source_folder: str | None = None, author: str = "model") -> dict:
    """Write one teaching file. The version it replaces is kept in history/ with who wrote
    it, so the model's draft and the user's corrected version stay side by side."""
    if author not in ("model", "user"):
        raise ValueError("author must be model or user")
    if not text.strip():
        raise ValueError("nothing to write")
    p = file_path(kind, source_folder)
    history = os.path.join(os.path.dirname(p), "history")
    os.makedirs(history, exist_ok=True)
    if os.path.isfile(p):
        shutil.copy2(p, os.path.join(history, f"{kind}-{job.stamp()}-replaced.md"))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    shutil.copy2(p, os.path.join(history, f"{kind}-{job.stamp()}-{author}.md"))
    return {"file": p, "kind": kind, "author": author, "characters": len(text)}


# Capture through the TCAP routine


def capture(com, action: str, folder: str = "") -> dict:
    """Start, query or stop TCAP in the active drawing. TCAP.lsp (settings: capture_file)
    is loaded from the trusted folder when its functions are not defined yet."""
    if action not in CAPTURE_ACTIONS:
        raise ValueError(f"action must be one of {sorted(CAPTURE_ACTIONS)}")
    function = CAPTURE_ACTIONS[action]
    loaded = None
    if not com.defined([function]):
        loaded = com.load_lisp(job.routine_path(settings.get("capture_file")))
        if not com.defined([function]):
            raise RuntimeError(f"{os.path.basename(loaded)} loaded, but {function} is not defined in it")
    try:
        result = com.call_lisp(function, [folder] if action == "start" else [])
    except RuntimeError as e:
        result = f"ERROR: {e}"
    out = {"action": action, "ok": not result.startswith("ERROR"), "result": result}
    if loaded:
        out["loaded"] = loaded
    return out
