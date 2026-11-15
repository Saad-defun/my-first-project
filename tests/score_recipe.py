"""Score a model's recipe for a test job against that job's expected.yaml, item by item, and
append one line to tests\\results.csv so model and prompt changes can be compared over time.

    python tests\\score_recipe.py <recipe.yaml> --model "Qwen3.6-35B-A3B UD-Q2_K_XL"
           [--job job001] [--prompt v4] [--answer reply.txt] [--note "..."]

The recipe may be a saved recipe file ({"steps": [...]}) or a plain YAML list of steps, as a
model writes it in its reply. Each item is "correct" or "wrong"; items a script cannot judge
are "manual". When you later correct the model's draft, mark those items "corrected" in the
CSV by hand.
"""
import argparse
import csv
import os
import re
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def kind(step: dict) -> str:
    """What a step does, in the terms of expected.yaml's "order"."""
    if "change_layer" in step or "rename_layer" in step:
        return "change_layer"
    if "lisp_call" in step:
        return "tag_fill" if step["lisp_call"].upper().startswith("GTAGA") else "details"
    if "lisp" in step:
        return "tag_fill" if step["lisp"].upper().startswith("GTAG") else "details"
    if "command" in step:
        return "purge" if "PURGE" in step["command"].upper() else "other"
    return "other"


def score(steps: list, exp: dict, answer: str | None) -> list[tuple[str, str, str]]:
    items = []

    def add(name, ok, detail=""):
        items.append((name, "correct" if ok else "wrong", detail))

    text = yaml.safe_dump(steps, allow_unicode=True)

    # Layer moves: every expected xref layer moved to its exact plain name
    moves = {}
    for s in steps:
        if "change_layer" in s:
            layer = (s["change_layer"].get("select") or {}).get("layer")
            moves[str(layer)] = s["change_layer"].get("to")
        if "rename_layer" in s:
            moves[str(s["rename_layer"].get("from"))] = s["rename_layer"].get("to")
    for src, plain in exp["xref_moves"].items():
        got = moves.get(src)
        add(f"move {src}", got == plain, f"got {got!r}, expected {plain!r}")
    for keep in exp.get("xref_keep", []):
        add(f"keep {keep}", keep not in moves and keep not in text.replace("\n", " "), "")

    # No deleting objects to clean up layers
    deletes = [s for s in steps if "delete" in s]
    add("no delete steps", not deletes, f"{len(deletes)} delete step(s)")

    # No separate tag-layer steps
    tag_prefix = exp["tag_layers"]["names"][0].rsplit(" ", 1)[0]
    tag_steps = [s for s in steps if tag_prefix in yaml.safe_dump(s)]
    add("no tag-layer steps", not tag_steps, f"{len(tag_steps)} step(s) name a tag layer")

    # Tag filling through GTAGA with a run parameter
    fill = [s for s in steps if kind(s) == "tag_fill"]
    fn = exp["tag_fill"]["function"]
    add("tags filled by " + fn, any(s.get("lisp_call") == fn for s in fill),
        f"tag steps: {[s.get('lisp_call') or s.get('lisp') for s in fill]}")
    if exp["tag_fill"].get("param_required"):
        args = [a for s in fill for a in s.get("args", []) + s.get("answers", [])]
        add("MH name as a {parameter}", bool(args) and all(re.search(r"\{\w+\}", str(a)) for a in args),
            f"args {args}")

    # No job values typed into the recipe
    found = sorted({m.group(0) for p in exp["forbidden_values"] for m in re.finditer(p, text)})
    add("no job values in the recipe", not found, f"found {found[:5]}")

    # Order of the kinds of step
    seq = [k for k in (kind(s) for s in steps) if k in exp["order"]]
    first = {k: seq.index(k) for k in reversed(seq)}
    last = {k: len(seq) - 1 - seq[::-1].index(k) for k in seq}
    present = [k for k in exp["order"] if k in seq]
    ordered = all(last[a] < first[b] for a, b in zip(present, present[1:]))
    add("step order " + " -> ".join(exp["order"]), ordered and seq[-1:] == ["purge"], f"got {seq}")
    items.append(("details from templates", "manual" if "details" in seq else "wrong",
                  "check the details step by eye" if "details" in seq else "no details step"))

    # The reply's own statements, if given
    if answer is not None:
        n = exp["tag_layers"]["count"]
        add(f"answer says {n} tag layers", re.search(rf"\b{n}\b[^.\n]{{0,40}}tag", answer, re.I) is not None, "")
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe")
    ap.add_argument("--job", default="job001")
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", default="", help="system prompt version, e.g. v4")
    ap.add_argument("--answer", help="the model's reply as text, to check its statements")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    with open(os.path.join(HERE, a.job, "expected.yaml"), encoding="utf-8") as f:
        exp = yaml.safe_load(f)
    with open(a.recipe, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    steps = data["steps"] if isinstance(data, dict) else data
    answer = open(a.answer, encoding="utf-8").read() if a.answer else None

    items = score(steps, exp, answer)
    for name, result, detail in items:
        print(f"{result:8} {name}" + (f"  ({detail})" if detail and result != "correct" else ""))
    counts = {r: sum(1 for _, x, _ in items if x == r) for r in ("correct", "wrong", "manual")}
    print(f"\n{counts['correct']} correct, {counts['wrong']} wrong, {counts['manual']} manual")

    out = os.path.join(HERE, "results.csv")
    new = not os.path.exists(out)
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "job", "model", "prompt", "recipe", "correct", "wrong", "manual", "items", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), a.job, a.model, a.prompt, os.path.basename(a.recipe),
                    counts["correct"], counts["wrong"], counts["manual"],
                    "; ".join(f"{n}={r}" for n, r, _ in items), a.note])
    return 0


if __name__ == "__main__":
    sys.exit(main())
