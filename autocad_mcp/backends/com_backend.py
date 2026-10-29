"""COM backend: drives the AutoCAD 2018 session that is already running (Windows only)."""
import os
import re
import time
from collections import Counter

from .base import Backend, summary
from .selection import matches, part_keep

PROG_ID = "AutoCAD.Application.22"  # AutoCAD 2018
AC_2018_DWG = 64  # AcSaveAsType.ac2018_dwg
AC_2018_DXF = 65  # AcSaveAsType.ac2018_dxf
RPC_E_CALL_REJECTED = -2147418111  # AutoCAD is busy
NAME_RE = r"[A-Za-z0-9_\-]+"

# System variables a routine should leave as it found them
SYSVARS = ("CLAYER", "CECOLOR", "CELTYPE", "TEXTSTYLE", "DIMSTYLE", "OSMODE", "ORTHOMODE",
           "SNAPMODE", "CMDECHO", "ATTREQ", "ATTDIA", "FILEDIA", "PICKSTYLE", "INSUNITS",
           "LUNITS", "LUPREC", "AUNITS", "AUPREC")

# COM ObjectName -> DXF type name, so both backends report entity types the same way
COM_TO_DXF = {
    "AcDbLine": "LINE",
    "AcDbCircle": "CIRCLE",
    "AcDbArc": "ARC",
    "AcDbEllipse": "ELLIPSE",
    "AcDbSpline": "SPLINE",
    "AcDbPoint": "POINT",
    "AcDbPolyline": "LWPOLYLINE",
    "AcDb2dPolyline": "POLYLINE",
    "AcDb3dPolyline": "POLYLINE",
    "AcDbText": "TEXT",
    "AcDbMText": "MTEXT",
    "AcDbAttributeDefinition": "ATTDEF",
    "AcDbBlockReference": "INSERT",
    "AcDbMInsertBlock": "INSERT",
    "AcDbHatch": "HATCH",
    "AcDbSolid": "SOLID",
    "AcDbTrace": "TRACE",
    "AcDbFace": "3DFACE",
    "AcDbXline": "XLINE",
    "AcDbRay": "RAY",
    "AcDbLeader": "LEADER",
    "AcDbMLeader": "MULTILEADER",
    "AcDbTable": "ACAD_TABLE",
    "AcDbRasterImage": "IMAGE",
    "AcDbWipeout": "WIPEOUT",
    "AcDbViewport": "VIEWPORT",
    "AcDbRotatedDimension": "DIMENSION",
    "AcDbAlignedDimension": "DIMENSION",
    "AcDbRadialDimension": "DIMENSION",
    "AcDbDiametricDimension": "DIMENSION",
    "AcDb2LineAngularDimension": "DIMENSION",
    "AcDb3PointAngularDimension": "DIMENSION",
    "AcDbOrdinateDimension": "DIMENSION",
    "AcDbRadialDimensionLarge": "LARGE_RADIAL_DIMENSION",
    "AcDbArcDimension": "ARC_DIMENSION",
}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _lisp_str(text: str) -> str:
    return '"' + text.replace("\\", "/").replace('"', '\\"') + '"'


def _lisp_list(text: str) -> list[str]:
    """Parse a printed LISP list of names, e.g. "(A B)", as returned through USERS1."""
    text = text.strip()
    if text in ("", "nil"):
        return []
    return text.strip("()").split()


def _bbox(e):
    try:
        lo, hi = e.GetBoundingBox()
        return (lo[0], lo[1], hi[0], hi[1])
    except Exception:
        return None


def _retry(fn, timeout: float = 10.0):
    """Call fn, retrying while AutoCAD rejects COM calls because it is busy."""
    import pywintypes

    end = time.monotonic() + timeout
    while True:
        try:
            return fn()
        except pywintypes.com_error as e:
            if e.hresult != RPC_E_CALL_REJECTED or time.monotonic() > end:
                raise
            time.sleep(0.1)


class ComBackend(Backend):
    name = "com"

    def _app(self):
        try:
            import pythoncom
            import win32com.client
        except ImportError as e:
            raise RuntimeError("COM backend needs Windows with pywin32 installed") from e
        pythoncom.CoInitialize()
        try:
            return win32com.client.GetActiveObject(PROG_ID)
        except Exception as e:
            raise RuntimeError("AutoCAD 2018 is not running") from e

    def _doc(self):
        app = self._app()
        if _retry(lambda: app.Documents.Count) == 0:
            raise RuntimeError("No drawing is open in AutoCAD")
        return _retry(lambda: app.ActiveDocument)

    def _find_doc(self, path: str | None):
        """The open drawing at `path`, or the active drawing if no path is given."""
        if not path:
            return self._doc()
        for d in self._app().Documents:
            name = _retry(lambda d=d: d.FullName)
            if name and _norm(name) == _norm(path):
                return d
        raise RuntimeError(f"{os.path.abspath(path)} is not open in AutoCAD; open it with open_in_autocad first")

    def _wait_idle(self, doc, timeout: float = 30.0):
        end = time.monotonic() + timeout
        while _retry(lambda: doc.GetVariable("CMDACTIVE")) != 0:
            if time.monotonic() > end:
                raise TimeoutError("AutoCAD command did not finish (is it waiting for input?)")
            time.sleep(0.1)

    def _send(self, doc, text: str, timeout: float = 30.0):
        _retry(lambda: doc.SendCommand(text))
        try:
            self._wait_idle(doc, timeout)
        except TimeoutError:
            _retry(lambda: doc.SendCommand("\x03\x03"))  # cancel, so AutoCAD is usable again
            raise

    def open_documents(self) -> list[str]:
        app = self._app()
        names = [_retry(lambda d=d: d.FullName) for d in app.Documents]
        return [_norm(n) for n in names if n]

    def is_open(self, path: str) -> bool:
        """True if AutoCAD has this file open; False if not, or if AutoCAD is unavailable."""
        try:
            return _norm(path) in self.open_documents()
        except RuntimeError:
            return False

    def status(self, path: str | None = None) -> dict:
        app = self._app()
        return {
            "backend": self.name,
            "autocad_version": _retry(lambda: app.Version),
            "open_drawings": self.open_documents(),
        }

    def inspect(self, path: str | None = None) -> dict:
        doc = self._find_doc(path)
        self._wait_idle(doc)
        by_layer, by_type, block_refs = Counter(), Counter(), Counter()
        for e in doc.ModelSpace:
            kind = COM_TO_DXF.get(e.ObjectName, e.ObjectName)
            by_layer[e.Layer] += 1
            by_type[kind] += 1
            if kind == "INSERT":
                block_refs[e.Name] += 1
        layers = {
            layer.Name: {
                "color": layer.Color,
                "linetype": layer.Linetype,
                "on": bool(layer.LayerOn),
                "frozen": bool(layer.Freeze),
                "locked": bool(layer.Lock),
            }
            for layer in doc.Layers
        }
        return summary(
            drawing=doc.FullName or doc.Name,
            layers=layers,
            blocks=[b.Name for b in doc.Blocks if not b.Name.startswith("*")],
            block_refs=block_refs,
            text_styles=[s.Name for s in doc.TextStyles if s.Name],
            dim_styles=[s.Name for s in doc.DimStyles],
            linetypes=[lt.Name for lt in doc.Linetypes],
            by_layer=by_layer,
            by_type=by_type,
        )

    def empty_attributes(self, path: str | None = None) -> dict:
        """Block name -> attribute tags left empty in its references (model space)."""
        empty = {}
        for e in self._find_doc(path).ModelSpace:
            if e.ObjectName == "AcDbBlockReference" and e.HasAttributes:
                for a in e.GetAttributes():
                    if not a.TextString.strip():
                        empty.setdefault(e.Name, set()).add(a.TagString)
        return {k: sorted(v) for k, v in sorted(empty.items())}

    def sysvars(self, names=SYSVARS) -> dict:
        doc = self._doc()
        return {n: _retry(lambda n=n: doc.GetVariable(n)) for n in names}

    # Drawings

    def activate(self, path: str):
        """Open (or switch to) the drawing at `path` and return it once AutoCAD is idle."""
        self.open_drawing(path)
        doc = self._find_doc(path)
        _retry(doc.Activate)
        self._wait_idle(doc)
        return doc

    def save(self, path: str, as_dwg: bool = False) -> str:
        """Save the open drawing at `path` (DXF stays DXF R2018, otherwise DWG 2018).
        as_dwg saves it as a DWG 2018 beside it instead and returns the new path."""
        doc = self._find_doc(path)
        target = os.path.abspath(path)
        if as_dwg:
            target = os.path.splitext(target)[0] + ".dwg"
        fmt = AC_2018_DXF if target.lower().endswith(".dxf") else AC_2018_DWG
        _retry(lambda: doc.SaveAs(target, fmt))
        return target

    def close(self, path: str, save: bool = False):
        doc = self._find_doc(path)
        _retry(lambda: doc.Close(save))

    def reduce(self, path: str, layers=None, blocks=None, types=None, margin: float = 0.1) -> dict:
        """Cut the drawing at `path` down to a part (see selection.part_keep) and save it."""
        doc = self.activate(path)
        ents, items = {}, []
        for e in doc.ModelSpace:
            kind = COM_TO_DXF.get(e.ObjectName, e.ObjectName)
            items.append((e.Handle, e.Layer, kind, e.Name if kind == "INSERT" else None, _bbox(e)))
            ents[e.Handle] = e
        keep, region = part_keep(items, layers, blocks, types, margin)
        failed = 0
        for handle, e in ents.items():
            if handle not in keep:
                try:
                    _retry(e.Delete)
                except Exception:
                    failed += 1  # e.g. on a locked layer
        self.save(path)
        return {"kept": len(keep), "deleted": len(ents) - len(keep) - failed,
                "not_deleted": failed, "region": region}

    # Commands and recipe rules

    def run_command(self, command: str, answers: list[str] | None = None,
                    timeout: float = 30.0) -> dict:
        """Run an AutoCAD or LISP command in the active drawing, answering its prompts in
        order. An empty answer is Enter. Times out (and cancels) if it still waits for input."""
        if "\n" in command or any("\n" in a for a in answers or []):
            raise ValueError("command and answers must be single lines; give each answer separately")
        text = "\n".join([command, *(answers or [])]) + "\n"
        self._send(self._doc(), text, timeout)
        return {"command": command, "answers": answers or []}

    def apply_rule(self, rule: str, args: dict) -> dict:
        """Recipe rules done directly through COM: rename_layer, delete, change_layer."""
        doc = self._doc()
        if rule == "rename_layer":
            return self._rename_layer(doc, args["from"], args["to"])
        selector = args["select"] if rule == "change_layer" else args
        picked = [
            e for e in doc.ModelSpace
            if matches(selector, e.Layer, COM_TO_DXF.get(e.ObjectName, e.ObjectName),
                       e.Name if e.ObjectName in ("AcDbBlockReference", "AcDbMInsertBlock") else None)
        ]
        if rule == "change_layer":
            if args["to"].upper() not in {la.Name.upper() for la in doc.Layers}:
                _retry(lambda: doc.Layers.Add(args["to"]))
            action = lambda e: setattr(e, "Layer", args["to"])
        elif rule == "delete":
            action = lambda e: e.Delete()
        else:
            raise ValueError(f"unknown rule {rule!r}")
        failed = 0
        for e in picked:
            try:
                _retry(lambda e=e: action(e))
            except Exception:
                failed += 1
        return {"rule": rule, "entities": len(picked) - failed, "failed": failed}

    def _rename_layer(self, doc, old: str, new: str) -> dict:
        layers = {la.Name.upper(): la for la in doc.Layers}
        if old.upper() not in layers:
            return {"rule": "rename_layer", "skipped": f"layer {old} is not in the drawing"}
        if new.upper() not in layers:
            _retry(lambda: setattr(layers[old.upper()], "Name", new))
            return {"rule": "rename_layer", "renamed": f"{old} -> {new}"}
        # Target exists: move the entities across, then remove the old layer
        target = layers[new.upper()].Name
        moved = 0
        for e in doc.ModelSpace:
            if e.Layer.upper() == old.upper():
                _retry(lambda e=e: setattr(e, "Layer", target))
                moved += 1
        try:
            _retry(layers[old.upper()].Delete)
            note = "old layer deleted"
        except Exception:
            note = "old layer kept (still in use, e.g. inside blocks or current)"
        return {"rule": "rename_layer", "merged": f"{old} -> {target}", "entities": moved, "note": note}

    # Handoff

    def save_as_dxf(self, path: str, close_after: bool = True) -> str:
        doc = self._doc()
        target = os.path.abspath(path)
        _retry(lambda: doc.SaveAs(target, AC_2018_DXF))
        if close_after:
            _retry(lambda: doc.Close(False))
        return target

    def open_drawing(self, path: str) -> str:
        app = self._app()
        target = os.path.abspath(path)
        for d in app.Documents:
            if _retry(lambda d=d: d.FullName) and _norm(d.FullName) == _norm(target):
                _retry(d.Activate)
                return target
        _retry(lambda: app.Documents.Open(target))
        return target

    # LISP

    def load_lisp(self, path: str) -> str:
        """Load a .lsp file (already resolved to the trusted folder) into the active drawing.
        Raises with AutoCAD's message if the load fails."""
        result = self.run_lisp(
            "((lambda (r) (if (vl-catch-all-error-p r) (strcat \"ERROR: \" (vl-catch-all-error-message r)) \"OK\"))"
            f" (vl-catch-all-apply 'load (list {_lisp_str(os.path.abspath(path))})))"
        )
        if result != "OK":
            raise RuntimeError(f"loading {os.path.basename(path)} failed: {result}")
        return os.path.abspath(path)

    def run_lisp(self, expr: str, timeout: float = 30.0) -> str:
        """Evaluate a LISP expression and return its result as text (via USERS1)."""
        doc = self._doc()
        _retry(lambda: doc.SetVariable("USERS1", ""))
        self._send(doc, f'(setvar "USERS1" (vl-princ-to-string {expr})) ', timeout)
        return _retry(lambda: doc.GetVariable("USERS1"))

    def run_lisp_command(self, name: str) -> str:
        if not re.fullmatch(NAME_RE, name):
            raise ValueError(f"Invalid LISP command name: {name!r}")
        return self.run_lisp(f"(c:{name})")

    def defined(self, names: list[str]) -> list[str]:
        """Which of these function names are already defined in the active drawing."""
        if not names:
            return []
        quoted = " ".join(_lisp_str(n) for n in names)
        return _lisp_list(self.run_lisp(f"(vl-remove-if-not '(lambda (s) (boundp (read s))) (list {quoted}))"))

    def builtin_commands(self, names: list[str]) -> list[str]:
        """Which of these names are AutoCAD's own commands."""
        if not names:
            return []
        quoted = " ".join(_lisp_str(n) for n in names)
        return _lisp_list(self.run_lisp(f"(vl-remove-if-not 'getcname (list {quoted}))"))
