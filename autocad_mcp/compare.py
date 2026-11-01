"""Compare two drawing summaries (from inspect) and report the differences
using the words source and sample. Counts and names are given exactly, so a model reading
the report never has to count or retype a name."""
from collections import Counter

from entities import name_matches

DIRECTIONS = {
    "source_to_sample": ("source", "sample"),
    "sample_to_source": ("sample", "source"),
}

NAME_SECTIONS = ("text_styles", "dim_styles", "linetypes")
COUNT_SECTIONS = ("entities_by_layer", "entities_by_type")
XREF_BOUND = "$0$"  # AutoCAD names bound xref layers "<xref>$0$<name>"
TEXT_LIST_LIMIT = 30  # texts listed per layer and side; the exact count is always given


def _pairs(direction: str) -> list[tuple[str, str]]:
    if direction == "both":
        return list(DIRECTIONS.values())
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {[*DIRECTIONS, 'both']}, got {direction!r}")
    return [DIRECTIONS[direction]]


def compare(source: dict, sample: dict, direction: str = "source_to_sample",
            tag_pattern: str | None = None) -> dict:
    drawings = {"source": source, "sample": sample}
    reports = {f"{a} → {b}": _with_counts(drawings, a, b, tag_pattern) for a, b in _pairs(direction)}
    return reports if direction == "both" else next(iter(reports.values()))


def _with_counts(drawings: dict, base: str, target: str, tag_pattern: str | None) -> dict:
    """The full difference report, led by exact counts, the tag layers and the xref-bound
    layers with the plain name each one maps to."""
    a, b = drawings[base], drawings[target]
    report = _diff(drawings, base, target)
    counts = {}
    for section in ("layers", "blocks", *NAME_SECTIONS):
        n = {label.split(" ")[0]: len(items) for label, items in report[section].items()}
        counts[section] = {k: v for k, v in n.items() if v}
    counts.update({s: len(report[s]) for s in COUNT_SECTIONS})
    lead = {"summary": report.pop("summary"), "counts": counts}
    if tag_pattern:
        tags = {side: sorted(la for la in d["layers"] if name_matches(la, tag_pattern))
                for side, d in ((base, a), (target, b))}
        lead["tag_layers"] = {"pattern": tag_pattern,
                              "count": {side: len(v) for side, v in tags.items()}, **tags}
    bound = sorted({la for d in (a, b) for la, n in d["entities_by_layer"].items() if n and XREF_BOUND in la})
    if bound:
        lead["xref_bound_layers"] = {
            la: {"plain_name": la.split(XREF_BOUND)[-1],
                 "objects": {base: a["entities_by_layer"].get(la, 0), target: b["entities_by_layer"].get(la, 0)}}
            for la in bound
        }
    return {**lead, **report}


def compare_text(source: list[dict], sample: list[dict], direction: str = "source_to_sample") -> dict:
    """Compare the text of objects (records from list_entities) layer by layer: which texts
    are only in one drawing, and exact counts. Texts are compared whole, as written."""
    drawings = {"source": source, "sample": sample}
    reports = {f"{a} → {b}": _text_diff(drawings[a], drawings[b], a, b) for a, b in _pairs(direction)}
    return reports if direction == "both" else next(iter(reports.values()))


def _texts(records: list[dict]) -> dict[str, Counter]:
    out = {}
    for r in records:
        text = r.get("text")
        if text is None and r.get("attribs"):
            text = " | ".join(f"{k}={v}" for k, v in r["attribs"].items())
        if text is not None:
            out.setdefault(r["layer"], Counter())[text] += 1
    return out


def _text_diff(a: list[dict], b: list[dict], base: str, target: str) -> dict:
    ta, tb = _texts(a), _texts(b)
    layers, differ = {}, 0
    for la in sorted(set(ta) | set(tb)):
        x, y = ta.get(la, Counter()), tb.get(la, Counter())
        only_b, only_a = sorted((y - x).elements()), sorted((x - y).elements())
        entry = {"count": {base: sum(x.values()), target: sum(y.values())},
                 "same": sum((x & y).values())}
        for label, items in ((f"only in {target}", only_b), (f"only in {base}", only_a)):
            if items:
                entry[label] = items[:TEXT_LIST_LIMIT]
                if len(items) > TEXT_LIST_LIMIT:
                    entry[label + " (total)"] = len(items)
        if only_a or only_b:
            differ += 1
        layers[la] = entry
    return {
        "summary": f"{len(layers)} layers with text, {differ} differ ({base} → {target})",
        "direction": f"{base} → {target}",
        "layers": layers,
    }


def diff(a: dict, b: dict, base: str, target: str) -> dict:
    """Compare any two summaries under the given names, e.g. "before" and "after"."""
    return _diff({base: a, target: b}, base, target)


def diff_keys(report: dict) -> set[str]:
    """Flatten a report into keys like "layers/add/WALL" or "entities_by_layer/WALL",
    the same whichever names the two drawings had, so reports can be matched up."""
    keys = set()
    for section in ("layers", "blocks", *NAME_SECTIONS):
        for label, items in report[section].items():
            kind = label.split(" ")[0]  # add / remove / changed / references
            if section == "layers" and kind == "changed":
                keys.update(f"layers/changed/{name}/{p}" for name, props in items.items() for p in props)
            else:
                keys.update(f"{section}/{kind}/{name}" for name in items)
    for section in COUNT_SECTIONS:
        keys.update(f"{section}/{k}" for k in report[section])
    return keys


def involved(report: dict) -> dict:
    """Layers and blocks a source → sample report says the processing touches."""
    layers = {name for items in report["layers"].values() for name in items}
    layers |= set(report["entities_by_layer"])
    blocks = {name for items in report["blocks"].values() for name in items}
    return {"layers": sorted(layers), "blocks": sorted(blocks)}


def _diff(drawings: dict, base: str, target: str) -> dict:
    """Differences for turning the `base` drawing into the `target` drawing."""
    a, b = drawings[base], drawings[target]
    add, remove = f"add (only in {target})", f"remove (only in {base})"

    def names(x, y) -> dict:
        return {add: sorted(set(y) - set(x)), remove: sorted(set(x) - set(y))}

    def counts(x: dict, y: dict) -> dict:
        return {
            k: {base: x.get(k, 0), target: y.get(k, 0)}
            for k in sorted(set(x) | set(y))
            if x.get(k, 0) != y.get(k, 0)
        }

    layers = names(a["layers"], b["layers"])
    layers["changed"] = {}
    for name in sorted(set(a["layers"]) & set(b["layers"])):
        props = {
            p: {base: a["layers"][name][p], target: b["layers"][name][p]}
            for p in a["layers"][name]
            if a["layers"][name][p] != b["layers"][name][p]
        }
        if props:
            layers["changed"][name] = props

    blocks = names(a["blocks"], b["blocks"])
    common = set(a["blocks"]) & set(b["blocks"])
    blocks["references changed"] = counts(
        {k: v for k, v in a["blocks"].items() if k in common},
        {k: v for k, v in b["blocks"].items() if k in common},
    )

    report = {
        "direction": f"{base} → {target}",
        "drawings": {base: a["drawing"], target: b["drawing"]},
        "layers": layers,
        "blocks": blocks,
        **{s: names(a[s], b[s]) for s in NAME_SECTIONS},
        **{s: counts(a[s], b[s]) for s in COUNT_SECTIONS},
    }
    report["summary"] = _summary_line(report, base, target, add, remove)
    return report


def _summary_line(report: dict, base: str, target: str, add: str, remove: str) -> str:
    parts = []
    for section in ("layers", "blocks", *NAME_SECTIONS):
        n_add, n_remove = len(report[section][add]), len(report[section][remove])
        if n_add or n_remove:
            parts.append(f"{section}: {n_add} to add, {n_remove} to remove")
    if report["layers"]["changed"]:
        parts.append(f"layers changed: {len(report['layers']['changed'])}")
    for section in COUNT_SECTIONS:
        if report[section]:
            parts.append(f"{section}: {len(report[section])} differ")
    body = "; ".join(parts) if parts else "no differences found"
    return f"Turning the {base} into the {target}: {body}"
