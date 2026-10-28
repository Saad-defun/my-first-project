"""COM backend: drives the AutoCAD 2018 session that is already running (Windows only)."""
import os
import re
import time
from collections import Counter

from .base import Backend, summary

PROG_ID = "AutoCAD.Application.22"  # AutoCAD 2018
AC_2018_DXF = 65  # AcSaveAsType.ac2018_dxf
RPC_E_CALL_REJECTED = -2147418111  # AutoCAD is busy
LISP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lisp")

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

    def _send(self, doc, text: str):
        _retry(lambda: doc.SendCommand(text))
        self._wait_idle(doc)

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
        target = path if os.path.isabs(path) else os.path.join(LISP_DIR, path)
        target = os.path.abspath(target).replace("\\", "/")
        self._send(self._doc(), f'(load "{target}") ')
        return target

    def run_lisp(self, expr: str) -> str:
        """Evaluate a LISP expression and return its result as text (via USERS1)."""
        doc = self._doc()
        _retry(lambda: doc.SetVariable("USERS1", ""))
        self._send(doc, f'(setvar "USERS1" (vl-princ-to-string {expr})) ')
        return _retry(lambda: doc.GetVariable("USERS1"))

    def run_lisp_command(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise ValueError(f"Invalid LISP command name: {name!r}")
        return self.run_lisp(f"(c:{name})")
