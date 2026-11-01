"""ezdxf backend: reads and writes DXF files directly, without AutoCAD."""
import os
from collections import Counter

import ezdxf
from ezdxf import bbox

from entities import plain, record

from .base import Backend, summary
from .com_backend import ComBackend
from .selection import part_keep

DXF_VERSION = "R2018"


def _record(e) -> dict:
    """Turn one ezdxf entity into the shared record: position, cleaned text, points."""
    kind, dxf = e.dxftype(), e.dxf
    base = (dxf.handle, kind, dxf.get("layer", "0"))
    try:
        if kind in ("TEXT", "ATTDEF"):
            return record(*base, dxf.get("insert"), dxf.get("text", ""))
        if kind == "MTEXT":
            return record(*base, dxf.get("insert"), plain(e.text))
        if kind == "MULTILEADER":
            mtext = e.context.mtext
            if mtext is not None:
                return record(*base, mtext.insert, plain(mtext.default_content))
            return record(*base, content="block")
        if kind == "INSERT":
            return record(*base, dxf.insert, block=dxf.name,
                          attribs={a.dxf.tag: a.dxf.get("text", "") for a in e.attribs})
        if kind == "LINE":
            return record(*base, dxf.start, points=[dxf.start, dxf.end])
        if kind == "LWPOLYLINE":
            pts = [(x, y) for x, y, *_ in e.get_points()]
            return record(*base, pts[0] if pts else None, points=pts, closed=e.closed or None)
        if kind == "POLYLINE":
            pts = [tuple(p) for p in e.points()]
            return record(*base, pts[0] if pts else None, points=pts, closed=e.is_closed or None)
        if kind in ("CIRCLE", "ARC"):
            return record(*base, dxf.center, radius=round(dxf.radius, 3))
        if kind == "DIMENSION":
            override = dxf.get("text", "")
            return record(*base, dxf.get("defpoint"),
                          override if override not in ("", "<>") else None)
        for attr in ("insert", "center", "start", "location"):
            if dxf.hasattr(attr):
                return record(*base, dxf.get(attr))
    except Exception:
        pass
    return record(*base)


class EzdxfBackend(Backend):
    name = "ezdxf"

    def load(self, path: str, create: bool = False):
        if not os.path.exists(path):
            if not create:
                raise FileNotFoundError(path)
            return ezdxf.new(DXF_VERSION, setup=True)
        return ezdxf.readfile(path)

    def save(self, doc, path: str) -> str:
        """Save, refusing if AutoCAD has the same file open."""
        target = os.path.abspath(path)
        if ComBackend().is_open(target):
            raise RuntimeError(f"{target} is open in AutoCAD; close it before editing with ezdxf")
        doc.saveas(target)
        return target

    def status(self, path: str | None = None) -> dict:
        if not path:
            raise ValueError("ezdxf backend needs a path to a DXF file")
        doc = self.load(path)
        return {
            "backend": self.name,
            "path": os.path.abspath(path),
            "dxfversion": doc.dxfversion,
            "entities": len(doc.modelspace()),
        }

    def inspect(self, path: str | None = None) -> dict:
        if not path:
            raise ValueError("ezdxf backend needs a path to a DXF file")
        doc = self.load(path)
        by_layer, by_type, block_refs = Counter(), Counter(), Counter()
        for e in doc.modelspace():
            kind = e.dxftype()
            by_layer[e.dxf.get("layer", "0")] += 1
            by_type[kind] += 1
            if kind == "INSERT":
                block_refs[e.dxf.name] += 1
        layers = {
            layer.dxf.name: {
                "color": layer.color,
                "linetype": layer.dxf.linetype,
                "on": layer.is_on(),
                "frozen": layer.is_frozen(),
                "locked": layer.is_locked(),
            }
            for layer in doc.layers
        }
        return summary(
            drawing=os.path.abspath(path),
            layers=layers,
            blocks=[b.name for b in doc.blocks if not b.name.startswith("*")],
            block_refs=block_refs,
            text_styles=[s.dxf.name for s in doc.styles if s.dxf.name],
            dim_styles=[s.dxf.name for s in doc.dimstyles],
            linetypes=[lt.dxf.name for lt in doc.linetypes],
            by_layer=by_layer,
            by_type=by_type,
        )

    def entities(self, path: str | None = None) -> list[dict]:
        """One record per model space object (see entities.record)."""
        if not path:
            raise ValueError("ezdxf backend needs a path to a DXF file")
        return [_record(e) for e in self.load(path).modelspace()]

    def empty_attributes(self, path: str | None = None) -> dict:
        """Block name -> attribute tags left empty in its references (model space)."""
        if not path:
            raise ValueError("ezdxf backend needs a path to a DXF file")
        empty = {}
        for e in self.load(path).modelspace().query("INSERT"):
            for a in e.attribs:
                if not a.dxf.get("text", "").strip():
                    empty.setdefault(e.dxf.name, set()).add(a.dxf.tag)
        return {k: sorted(v) for k, v in sorted(empty.items())}

    def reduce(self, path: str, layers=None, blocks=None, types=None, margin: float = 0.1) -> dict:
        """Cut the DXF at `path` down to a part (see selection.part_keep) and save it."""
        doc = self.load(path)
        msp = doc.modelspace()
        ents, items = {}, []
        for e in msp:
            kind = e.dxftype()
            try:
                ext = bbox.extents([e])
                box = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y) if ext.has_data else None
            except Exception:
                box = None
            items.append((e.dxf.handle, e.dxf.get("layer", "0"), kind,
                          e.dxf.name if kind == "INSERT" else None, box))
            ents[e.dxf.handle] = e
        keep, region = part_keep(items, layers, blocks, types, margin)
        for handle, e in ents.items():
            if handle not in keep:
                msp.delete_entity(e)
        self.save(doc, path)
        return {"kept": len(keep), "deleted": len(ents) - len(keep), "region": region}
