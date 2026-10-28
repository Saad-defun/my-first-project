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
