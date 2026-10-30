"""The job workflow on top of the backends: test copies, routine tests, recipe runs,
approval, the conventions check and the job summary. Nothing here edits the source or
sample drawings: every change is made on a copy in test/ or shop/."""
import json
import os
import re
import shutil

import job
import recipe as recipes
import settings
from compare import compare, diff, diff_keys, involved

COMMAND_RE = r"[A-Za-z0-9_\-]+"


def _open(b, path: str):
    """com reads drawings that are open in AutoCAD, so open (or switch to) it first."""
    if b.name == "com":
        b.activate(path)


def _summary(b, path: str) -> dict:
    _open(b, path)
    return b.inspect(path)


def _com(b, tool: str):
    if b.name != "com":
        raise ValueError(f"{tool} only works with backend='com' (it runs LISP and commands in AutoCAD)")
    return b


def _run(com, command: str, answers, timeout: float) -> dict:
    try:
        com.run_command(command, answers, timeout)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Test copies


def make_test_copy(b, source_folder: str, source: str, mode: str = "full", sample: str | None = None,
                   layers=None, blocks=None, types=None, margin: float = 0.1) -> dict:
    """Copy the source into test/, whole or cut down to a part. Without layers, blocks or
    types, a part covers what the source → sample comparison says the processing touches."""
    src = job.resolve(source_folder, source)
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    if mode not in ("full", "part"):
        raise ValueError("mode must be 'full' or 'part'")
    stem, ext = os.path.splitext(os.path.basename(src))
    if mode == "part" and b.name == "ezdxf" and ext.lower() != ".dxf":
        raise ValueError("ezdxf can only cut a DXF; use backend='com' for a DWG")
    result = {"source": src, "mode": mode}
    if mode == "part" and not (layers or blocks or types):
        if not sample:
            raise ValueError("a part copy needs layers, blocks or types, or a sample to derive them from")
        report = compare(_summary(b, src), _summary(b, job.resolve(source_folder, sample)))
        picked = involved(report)
        layers, blocks = picked["layers"], picked["blocks"]
        result["derived_from_sample"] = picked
    dest = os.path.join(job.sub(source_folder, "test"), f"{stem}-test-{mode}-{job.stamp()}{ext}")
    shutil.copy2(src, dest)
    result["copy"] = dest
    if mode == "part":
        result.update(b.reduce(dest, layers, blocks, types, margin))
        result["kept_by"] = {"layers": layers or [], "blocks": blocks or [], "types": types or [], "margin": margin}
    return result


# Routine tests


def test_routine(b, source_folder: str, copy: str, routine: str, command: str, answers=None,
                 sample: str | None = None, run_twice: bool = True, timeout: float = 30.0,
                 close_after: bool = False) -> dict:
    """Test one routine on a test copy: loads cleanly, runs unattended, does the job,
    changes nothing else, safe to run twice. Failures are flagged, never blocking."""
    com = _com(b, "test_routine")
    copy = job.resolve(source_folder, copy)
    if not job.inside(copy, job.sub(source_folder, "test")):
        raise ValueError("routines are only tested on a copy in test/; make one with make_test_copy")
    if not re.fullmatch(COMMAND_RE, command):
        raise ValueError(f"invalid command name {command!r} (give it without c:)")
    file = job.routine_path(routine)
    report = {
        "kind": "test",
        "routine": os.path.relpath(file, job.lisp_dir()),
        "command": command,
        "answers": answers or [],
        "sha256": job.file_hash(file),
        "copy": copy,
        "sample": job.resolve(source_folder, sample) if sample else None,
        "cad": com.cad_info(),
        "started": job.now(),
    }
    checks = report["checks"] = {}
    sample_summary = _summary(com, report["sample"]) if sample else None
    com.activate(copy)

    # 1. Loads cleanly
    defuns = job.defun_names(file)
    commands = [d[2:].upper() for d in defuns if d.startswith("c:")]
    loads = checks["loads_cleanly"] = {
        "defines": defuns,
        "already_defined_in_drawing": com.defined(defuns),
        "same_name_in_other_trusted_files": job.defun_clashes(file),
        "redefines_autocad_command": com.builtin_commands(commands),
        "command_defined_by_file": command.upper() in commands,
    }
    try:
        com.load_lisp(file)
        loads["load"] = "OK"
    except Exception as e:
        loads["load"] = str(e)
    loads["pass"] = (loads["load"] == "OK" and loads["command_defined_by_file"]
                     and not loads["already_defined_in_drawing"]
                     and not loads["same_name_in_other_trusted_files"]
                     and not loads["redefines_autocad_command"])

    if loads["load"] == "OK":
        before, vars_before = com.inspect(copy), com.sysvars()

        # 2. Runs unattended
        run1 = _run(com, command, answers, timeout)
        checks["runs_unattended"] = {"pass": run1["ok"], **run1}
        after, vars_after = com.inspect(copy), com.sysvars()
        changes = diff_keys(diff(before, after, "before", "after"))
        sysvars_changed = {k: {"before": vars_before[k], "after": vars_after[k]}
                           for k in vars_before if vars_before[k] != vars_after[k]}

        # 3. Does the job, 4. changes nothing else
        if sample_summary:
            needed = diff_keys(diff(before, sample_summary, "before", "sample"))
            remaining = diff_keys(diff(after, sample_summary, "after", "sample"))
            checks["does_the_job"] = {
                "pass": bool(changes & needed),
                "changes_towards_sample": sorted(changes & needed),
                "differences_from_sample": {"before": len(needed), "after": len(remaining)},
            }
            unexpected = sorted(changes - needed)
        else:
            checks["does_the_job"] = {"pass": None, "note": "no sample given; review the changes yourself",
                                      "changes": sorted(changes)}
            unexpected = []
        checks["changes_nothing_else"] = {
            "pass": not unexpected and not sysvars_changed,
            "unexpected_changes": unexpected,
            "sysvars_changed": sysvars_changed,
        }
        if not sample_summary:
            checks["changes_nothing_else"]["note"] = "drawing changes not judged without a sample"

        # 5. Safe to run twice
        if run_twice and run1["ok"]:
            run2 = _run(com, command, answers, timeout)
            again = sorted(diff_keys(diff(after, com.inspect(copy), "first run", "second run")))
            checks["safe_to_run_twice"] = {"pass": run2["ok"] and not again,
                                           "changes_on_second_run": again, **run2}

    report["flags"] = [name for name, c in checks.items() if c["pass"] is False]
    unjudged = [name for name, c in checks.items() if c["pass"] is None]
    report["result"] = "flagged" if report["flags"] else ("needs review" if unjudged else "pass")
    report["finished"] = job.now()
    report["recorded"] = job.record_routine(source_folder, file)
    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", os.path.splitext(report["routine"])[0])
    job.write_report(source_folder, "test", f"test-{stem}-{command}", report)
    if close_after:
        com.close(copy, save=False)
    else:
        report["note"] = "the test copy is left open in AutoCAD, unsaved, for you to look at"
    return report


# Recipes


def save_recipe(source_folder: str, name: str, steps: list, notes: str = "") -> dict:
    """Write a recipe as a draft. Any earlier test result or approval is cleared."""
    errors = recipes.validate(steps)
    if errors:
        raise ValueError("recipe not saved: " + "; ".join(errors))
    data = {"name": name, "status": "draft", "updated": job.now(), "notes": notes, "steps": steps}
    path = recipes.save(source_folder, name, data)
    missing = [f for f, h in recipes.current_hashes(data).items() if h is None]
    return {"recipe": path, "status": "draft", "steps": len(steps),
            "routines_missing_from_trusted_folder": missing}


def get_recipe(source_folder: str, name: str) -> dict:
    data = recipes.load(source_folder, name)
    data["routines_changed_since_approval"] = recipes.hash_changes(data)
    return data


def run_recipe(b, source_folder: str, name: str, drawing: str, mode: str = "test",
               sample: str | None = None, timeout: float = 60.0) -> dict:
    """Run a recipe on a copy of `drawing`. test: the copy goes to test/ and is always
    allowed. full: needs an approved recipe whose routines are unchanged; the result is
    saved as a DWG 2018 shop drawing in shop/."""
    com = _com(b, "run_recipe")
    if mode not in ("test", "full"):
        raise ValueError("mode must be 'test' or 'full'")
    r = recipes.load(source_folder, name)
    errors = recipes.validate(r.get("steps"))
    if errors:
        raise ValueError("recipe is invalid: " + "; ".join(errors))
    changed = recipes.hash_changes(r)
    cad = com.cad_info()
    warnings = []
    if mode == "full":
        if r.get("status") != "approved":
            raise RuntimeError(f"recipe {name!r} is {r.get('status')}; test it and have it approved first")
        if changed:
            raise RuntimeError(f"routines changed since approval: {changed}; test the recipe again and re-approve")
        tested_on = (r.get("approved") or {}).get("cad")
        if tested_on and (tested_on["product"], tested_on["version"]) != (cad["product"], cad["version"]):
            warnings.append(f"recipe was tested on {tested_on['product']} {tested_on['version']}, "
                            f"this run is on {cad['product']} {cad['version']}")

    src = job.resolve(source_folder, drawing)
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    stem, ext = os.path.splitext(os.path.basename(src))
    out_dir = job.sub(source_folder, "test" if mode == "test" else "shop")
    out = os.path.join(out_dir, f"{stem}-{'recipe-' + name if mode == 'test' else 'shop'}-{job.stamp()}{ext}")
    shutil.copy2(src, out)
    sample_summary = _summary(com, job.resolve(source_folder, sample)) if sample else None

    com.activate(out)
    loaded, steps = set(), []
    for i, step in enumerate(r["steps"], 1):
        t = recipes.step_type(step)
        entry = {"step": i, "type": t, **step}
        try:
            if t == "lisp":
                file = job.routine_path(step["file"])
                if file not in loaded:
                    com.load_lisp(file)
                    loaded.add(file)
                com.run_command(step["lisp"], step.get("answers"), timeout)
                tested = job.latest_test(source_folder, step["file"], step["lisp"])
                entry["routine_test"] = tested or "not tested"
                if tested and tested["sha256"] != job.file_hash(file):
                    entry["routine_test"]["note"] = "tested on a different version of the file"
            elif t == "command":
                com.run_command(step["command"], step.get("answers"), timeout)
            else:
                entry["result"] = com.apply_rule(t, step[t])
            entry["ok"] = True
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        steps.append(entry)

    if mode == "full":
        saved = com.save(out, as_dwg=True, file_format=settings.get("save_format"))
    else:
        saved = com.save(out)
    if os.path.normcase(saved) != os.path.normcase(out):
        try:
            os.remove(out)  # the DXF work copy, now saved as DWG
        except OSError:
            pass
    report = {
        "kind": f"recipe-{mode}",
        "recipe": name,
        "recipe_status": r.get("status"),
        "drawing": src,
        "output": saved,
        "cad": cad,
        "warnings": warnings,
        "ran": job.now(),
        "steps": steps,
        "steps_failed": [s["step"] for s in steps if not s["ok"]],
        "steps_with_flagged_routines": [s["step"] for s in steps if isinstance(s.get("routine_test"), dict)
                                        and s["routine_test"]["result"] != "pass"],
        "steps_with_untested_routines": [s["step"] for s in steps if s.get("routine_test") == "not tested"],
        "routines_changed_since_approval": changed,
        "recorded": [job.record_routine(source_folder, f) for f in sorted(loaded)],
    }
    if sample_summary:
        result = com.inspect(saved)
        remaining = diff(result, sample_summary, "result", "sample")
        report["matches_sample"] = not diff_keys(remaining)
        report["remaining_differences"] = remaining
        report["not_covered"] = {la: n for la, n in result["entities_by_layer"].items()
                                 if la not in sample_summary["layers"]}
    job.write_report(source_folder, "test" if mode == "test" else "shop", f"recipe-{name}-{mode}", report)

    if mode == "test" and r.get("status") != "approved":
        r["status"] = "tested"
        r["last_test"] = {"at": report["ran"], "report": report["report_file"],
                          "steps_failed": report["steps_failed"],
                          "matches_sample": report.get("matches_sample"),
                          "cad": cad}
        recipes.save(source_folder, name, r)
    report["note"] = "the result is left open in AutoCAD for review"
    return report


def approve_recipe(source_folder: str, name: str, approved_by: str) -> dict:
    """Mark a tested recipe approved and lock its routines' hashes. Open flags stay recorded."""
    if not approved_by.strip():
        raise ValueError("approved_by must name the person approving")
    r = recipes.load(source_folder, name)
    if r.get("status") != "tested":
        raise RuntimeError(f"recipe {name!r} is {r.get('status')}; only a tested recipe can be approved")
    hashes = recipes.current_hashes(r)
    missing = [f for f, h in hashes.items() if h is None]
    if missing:
        raise RuntimeError(f"routines missing from the trusted folder: {missing}")
    last = r.get("last_test") or {}
    r["status"] = "approved"
    r["approved"] = {"by": approved_by, "at": job.now(), "hashes": hashes, "cad": last.get("cad"),
                     "open_flags": {"steps_failed": last.get("steps_failed", []),
                                    "matches_sample": last.get("matches_sample")}}
    recipes.save(source_folder, name, r)
    return {"recipe": name, "status": "approved", **r["approved"]}


# Final check and close


def check_conventions(b, drawing: str, sample: str) -> dict:
    """Check a drawing against the sample's conventions: the layers and styles the sample
    uses exist, nothing sits on layers the sample doesn't have, layer properties match,
    and title-block style attributes are filled in."""
    d, d_attrs = _summary(b, drawing), b.empty_attributes(drawing)
    s, s_attrs = _summary(b, sample), b.empty_attributes(sample)
    used = {la for la, n in s["entities_by_layer"].items() if n}
    report = {
        "drawing": d["drawing"],
        "sample": s["drawing"],
        "missing_layers": sorted(used - set(d["layers"])),
        "entities_on_layers_not_in_sample": {la: n for la, n in d["entities_by_layer"].items()
                                             if la not in s["layers"]},
        "layer_properties_differ": {
            la: {p: {"drawing": d["layers"][la][p], "sample": s["layers"][la][p]}
                 for p in d["layers"][la] if d["layers"][la][p] != s["layers"][la][p]}
            for la in sorted(set(d["layers"]) & set(s["layers"]))
            if d["layers"][la] != s["layers"][la]
        },
        "missing_text_styles": sorted(set(s["text_styles"]) - set(d["text_styles"])),
        "missing_dim_styles": sorted(set(s["dim_styles"]) - set(d["dim_styles"])),
        "unfilled_attributes": {blk: sorted(set(tags) - set(s_attrs.get(blk, [])))
                                for blk, tags in d_attrs.items()
                                if blk in s["blocks"] and set(tags) - set(s_attrs.get(blk, []))},
        "info": {"blocks_used_in_sample_not_in_drawing":
                 sorted(k for k, n in s["blocks"].items() if n and not d["blocks"].get(k))},
    }
    report["pass"] = not any(report[k] for k in ("missing_layers", "entities_on_layers_not_in_sample",
                                                 "layer_properties_differ", "missing_text_styles",
                                                 "missing_dim_styles", "unfilled_attributes"))
    return report


def close_job(b, source_folder: str, name: str, shop_drawing: str, sample: str,
              closed_by: str, notes: str = "") -> dict:
    """Run the final conventions check and write job-summary.json and job-summary.md
    in the source folder: recipe, routine versions, checks, open flags, who closed it."""
    if not closed_by.strip():
        raise ValueError("closed_by must name the person closing the job")
    r = recipes.load(source_folder, name)
    shop = job.resolve(source_folder, shop_drawing)
    conventions = check_conventions(b, shop, job.resolve(source_folder, sample))
    full_runs = [x for x in job.reports(source_folder, "shop", "recipe-full") if x["recipe"] == name]
    run = full_runs[-1] if full_runs else None
    summary = {
        "job": job.folder(source_folder),
        "closed_by": closed_by,
        "closed_at": job.now(),
        "notes": notes,
        "recipe": {"name": name, "status": r.get("status"), "approved": r.get("approved")},
        "routines": recipes.current_hashes(r),
        "routines_changed_since_approval": recipes.hash_changes(r),
        "shop_drawing": shop,
        "full_run": run and {"report": run["report_file"], "ran": run["ran"],
                             "steps_failed": run["steps_failed"],
                             "steps_with_flagged_routines": run["steps_with_flagged_routines"],
                             "steps_with_untested_routines": run["steps_with_untested_routines"]},
        "conventions": conventions,
    }
    open_flags = []
    if not run:
        open_flags.append("no full run of this recipe found in shop/")
    else:
        if run["steps_failed"]:
            open_flags.append(f"steps failed in the full run: {run['steps_failed']}")
        if run["steps_with_flagged_routines"]:
            open_flags.append(f"steps using flagged routines: {run['steps_with_flagged_routines']}")
        if run["steps_with_untested_routines"]:
            open_flags.append(f"steps using untested routines: {run['steps_with_untested_routines']}")
    if summary["routines_changed_since_approval"]:
        open_flags.append(f"routines changed since approval: {summary['routines_changed_since_approval']}")
    if not conventions["pass"]:
        open_flags.append("the shop drawing does not meet all of the sample's conventions")
    summary["open_flags"] = open_flags

    root = job.folder(source_folder)
    with open(os.path.join(root, "job-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(root, "job-summary.md"), "w", encoding="utf-8") as f:
        f.write(_summary_md(summary))
    summary["files"] = [os.path.join(root, "job-summary.json"), os.path.join(root, "job-summary.md")]
    return summary


def _summary_md(s: dict) -> str:
    approved = s["recipe"]["approved"] or {}
    lines = [
        f"# Job summary: {os.path.basename(s['job'])}",
        "",
        f"- **Closed:** {s['closed_at']} by {s['closed_by']}",
        f"- **Recipe:** {s['recipe']['name']} ({s['recipe']['status']}"
        + (f", approved {approved.get('at')} by {approved.get('by')})" if approved else ")"),
        f"- **Shop drawing:** {s['shop_drawing']}",
        f"- **Conventions check:** {'pass' if s['conventions']['pass'] else 'not met'}",
        "",
        "## Routines used",
        "",
        *[f"- `{f}`: {h or 'missing from trusted folder'}" for f, h in s["routines"].items()],
        "",
        "## Open flags",
        "",
        *([f"- {x}" for x in s["open_flags"]] or ["- None"]),
    ]
    if s["notes"]:
        lines += ["", "## Notes", "", s["notes"]]
    return "\n".join(lines) + "\n"
