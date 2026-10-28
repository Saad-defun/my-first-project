"""ezdxf backend: reads and writes DXF files directly, without AutoCAD."""
import os
from collections import Counter

import ezdxf

from .base import Backend, summary
from .com_backend import ComBackend

DXF_VERSION = "R2018"


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
