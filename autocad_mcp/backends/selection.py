"""Entity selectors (used by recipe rules) and the part-of-drawing selection used for test copies."""
from fnmatch import fnmatchcase

FIELDS = ("layer", "type", "block")


def _any(patterns, value: str | None) -> bool:
    if value is None:
        return False
    if isinstance(patterns, str):
        patterns = [patterns]
    return any(fnmatchcase(value.upper(), p.upper()) for p in patterns)


def matches(selector: dict, layer: str, kind: str, block: str | None) -> bool:
    """True if the entity meets every condition in the selector. Each condition is a
    wildcard pattern or a list of them, e.g. {"layer": "TEMP*", "type": ["TEXT", "MTEXT"]}."""
    if not selector:
        raise ValueError("selector is empty")
    unknown = set(selector) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown selector fields {sorted(unknown)}; use {list(FIELDS)}")
    values = {"layer": layer, "type": kind, "block": block}
    return all(_any(selector[f], values[f]) for f in selector)


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def part_keep(items: list[tuple], layers=None, blocks=None, types=None,
              margin: float = 0.1) -> tuple[set, tuple | None]:
    """Choose what a part copy keeps. items are (key, layer, type, block, bbox) with bbox
    (xmin, ymin, xmax, ymax) or None. Keeps every entity on the given layers, blocks or
    types, plus everything else inside their area grown by `margin` (a fraction of the
    area's larger side), so routines still see the context around them."""
    selectors = [{f: v} for f, v in (("layer", layers), ("block", blocks), ("type", types)) if v]
    if not selectors:
        raise ValueError("a part copy needs layers, blocks or types to keep")
    core = {k for k, la, ki, bl, _ in items if any(matches(s, la, ki, bl) for s in selectors)}
    if not core:
        raise ValueError("nothing in the drawing matches the layers, blocks or types given")
    boxes = [b for k, *_, b in items if k in core and b]
    if not boxes:
        return core, None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    pad = max(x1 - x0, y1 - y0) * margin
    region = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    return core | {k for k, *_, b in items if b and _overlaps(b, region)}, region
