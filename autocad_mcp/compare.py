"""Compare two drawing summaries (from inspect) and report the differences
using the words source and sample."""

DIRECTIONS = {
    "source_to_sample": ("source", "sample"),
    "sample_to_source": ("sample", "source"),
}

NAME_SECTIONS = ("text_styles", "dim_styles", "linetypes")
COUNT_SECTIONS = ("entities_by_layer", "entities_by_type")


def compare(source: dict, sample: dict, direction: str = "source_to_sample") -> dict:
    drawings = {"source": source, "sample": sample}
    if direction == "both":
        return {f"{a} → {b}": _diff(drawings, a, b) for a, b in DIRECTIONS.values()}
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {[*DIRECTIONS, 'both']}, got {direction!r}")
    return _diff(drawings, *DIRECTIONS[direction])


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
