"""AutoCAD MCP server. Every tool takes an explicit backend: "com" or "ezdxf"."""
from typing import Literal

from mcp.server.fastmcp import FastMCP

from backends import BACKENDS
from compare import compare

mcp = FastMCP("autocad")

BackendName = Literal["com", "ezdxf"]
Direction = Literal["source_to_sample", "sample_to_source", "both"]


def _backend(name: str):
    if name not in BACKENDS:
        raise ValueError(f"backend must be one of {sorted(BACKENDS)}, got {name!r}")
    return BACKENDS[name]


def _com_only(name: str, tool: str):
    if name != "com":
        raise ValueError(f"{tool} only works with backend='com'")
    return BACKENDS["com"]


@mcp.tool()
def backend_status(backend: BackendName, path: str | None = None) -> dict:
    """Check a backend is usable. com: reports the running AutoCAD 2018 session.
    ezdxf: needs `path` to a DXF file and reports its version and entity count."""
    return _backend(backend).status(path)


# Inspect and compare


@mcp.tool()
def inspect_drawing(backend: BackendName, path: str | None = None) -> dict:
    """Summarise a drawing's model space: layers (color, linetype, on/frozen/locked),
    blocks and their reference counts, text/dim styles, linetypes, and entity counts
    per layer and per type. ezdxf: `path` to a DXF file is required. com: `path`
    picks an open drawing; without it the active drawing is used."""
    return _backend(backend).inspect(path)


@mcp.tool()
def compare_drawings(backend: BackendName, source: str, sample: str,
                     direction: Direction = "source_to_sample") -> dict:
    """Compare the source drawing with the sample drawing.
    direction: "source_to_sample" (what to add/remove/change to turn the source into
    the sample), "sample_to_source" (the reverse comparison), or "both".
    com: both drawings must be open in AutoCAD."""
    b = _backend(backend)
    return compare(b.inspect(source), b.inspect(sample), direction)


# Handoff between backends


@mcp.tool()
def save_as_dxf(backend: BackendName, path: str, close_after: bool = True) -> str:
    """Save the active AutoCAD drawing as DXF R2018 so ezdxf can work on it.
    close_after closes it in AutoCAD so ezdxf is allowed to edit the file. com only."""
    return _com_only(backend, "save_as_dxf").save_as_dxf(path, close_after)


@mcp.tool()
def open_in_autocad(backend: BackendName, path: str) -> str:
    """Open (or activate, if already open) a DWG/DXF file in AutoCAD. com only."""
    return _com_only(backend, "open_in_autocad").open_drawing(path)


# LISP (com only)


@mcp.tool()
def load_lisp(backend: BackendName, path: str) -> str:
    """Load a .lsp file. Relative paths resolve to the server's lisp/ folder. com only."""
    return _com_only(backend, "load_lisp").load_lisp(path)


@mcp.tool()
def run_lisp(backend: BackendName, expr: str) -> str:
    """Evaluate a LISP expression in AutoCAD and return its result as text. com only."""
    return _com_only(backend, "run_lisp").run_lisp(expr)


@mcp.tool()
def run_lisp_command(backend: BackendName, name: str) -> str:
    """Run a loaded LISP command (defun c:NAME) and return its result as text. com only."""
    return _com_only(backend, "run_lisp_command").run_lisp_command(name)


if __name__ == "__main__":
    mcp.run()
