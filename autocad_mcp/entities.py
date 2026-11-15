"""Reading individual objects, shared by both backends: one record format, name matching,
filters and MText cleaning. Backends only turn their own objects into raw records."""
from collections import Counter
from fnmatch import fnmatchcase

from ezdxf.tools.text import plain_mtext

MAX_POINTS = 50  # polyline vertices returned per object; the full count is always given


def plain(raw: str | None) -> str | None:
    """Text without MText formatting codes; paragraph breaks become new lines."""
    if raw is None:
        return None
    return plain_mtext(raw, split=False).strip()


def rnd(xy) -> list[float]:
    return [round(float(xy[0]), 3), round(float(xy[1]), 3)]


def record(handle: str, kind: str, layer: str, pos=None, text: str | None = None, **extra) -> dict:
    """The record both backends return for one object. Empty parts are left out."""
    rec = {"handle": handle, "type": kind, "layer": layer}
    if pos is not None:
        rec["pos"] = rnd(pos)
    if text is not None:
        rec["text"] = text
    points = extra.pop("points", None)
    if points is not None:
        pts = [rnd(p) for p in points]
        rec["points"] = pts[:MAX_POINTS]
        if len(pts) > MAX_POINTS:
            rec["points_total"] = len(pts)
    rec.update({k: v for k, v in extra.items() if v not in (None, {}, [])})
    return rec


def name_matches(name: str | None, patterns) -> bool:
    """Case-insensitive match against a pattern or list of patterns. A pattern with * or ?
    is a wildcard; anything else must match the whole name exactly (so "$" and "[" in
    xref-bound layer names are taken literally)."""
    if not patterns:
        return True
    if name is None:
        return False
    if isinstance(patterns, str):
        patterns = [patterns]
    upper = name.upper()
    for p in patterns:
        p = p.upper()
        if "*" in p or "?" in p:
            if fnmatchcase(upper, p.replace("[", "[[]")):
                return True
        elif upper == p:
            return True
    return False


def text_matches(text: str | None, pattern: str | None) -> bool:
    """A wildcard pattern, or a plain substring when it has no * or ?. Case-insensitive."""
    if not pattern:
        return True
    if text is None:
        return False
    if "*" in pattern or "?" in pattern:
        return fnmatchcase(text.upper(), pattern.upper().replace("[", "[[]"))
    return pattern.upper() in text.upper()


def in_area(rec: dict, area) -> bool:
    if not area:
        return True
    if "pos" not in rec:
        return False
    x0, y0, x1, y1 = area
    x, y = rec["pos"]
    return min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1)


def select(records, layers=None, types=None, text=None, area=None) -> list[dict]:
    """Records on the given layers and of the given types, whose text matches and whose
    position is inside the area (x0, y0, x1, y1)."""
    if area is not None and len(area) != 4:
        raise ValueError("area must be [x0, y0, x1, y1]")
    return [r for r in records
            if name_matches(r["layer"], layers)
            and name_matches(r["type"], types)
            and text_matches(r.get("text") or " ".join((r.get("attribs") or {}).values()) or None, text)
            and in_area(r, area)]


def layout(drawing: str, records: list[dict], title_layer: str) -> dict:
    """Cut-sheet details found by their titles (TEXT or MTEXT on title_layer), left to right:
    the gap between neighbours, the typical spacing (median gap), the x range each detail
    takes (halfway to its neighbours) and where the next title would go."""
    titles = sorted((r for r in records
                     if name_matches(r["layer"], title_layer) and r["type"] in ("TEXT", "MTEXT")
                     and r.get("text") and "pos" in r),
                    key=lambda r: (r["pos"][0], r["pos"][1]))
    xs = [t["pos"][0] for t in titles]
    gaps = [round(b - a, 3) for a, b in zip(xs, xs[1:])]
    spacing = sorted(gaps)[len(gaps) // 2] if gaps else None
    half = spacing / 2 if spacing else None
    details = []
    for i, t in enumerate(titles):
        left = (xs[i - 1] + xs[i]) / 2 if i else (xs[i] - half if half else None)
        right = (xs[i] + xs[i + 1]) / 2 if i + 1 < len(xs) else (xs[i] + half if half else None)
        details.append({"title": t["text"], "handle": t["handle"], "pos": t["pos"],
                        "x_range": [round(v, 3) if v is not None else None for v in (left, right)]})
    rows = sorted({round(t["pos"][1], 1) for t in titles})
    return {
        "drawing": drawing,
        "title_layer": title_layer,
        "count": len(titles),
        "rows": len(rows),
        "spacing": spacing,
        "gaps": gaps,
        "details": details,
        "next_title_pos": [round(xs[-1] + spacing, 3), titles[-1]["pos"][1]] if spacing else None,
    }


def listing(drawing: str, picked: list[dict], limit: int) -> dict:
    """The list_entities result: exact counts first, then the records."""
    limit = max(1, int(limit))
    return {
        "drawing": drawing,
        "matched": len(picked),
        "returned": min(len(picked), limit),
        "by_layer": dict(sorted(Counter(r["layer"] for r in picked).items())),
        "by_type": dict(sorted(Counter(r["type"] for r in picked).items())),
        "entities": picked[:limit],
    }
