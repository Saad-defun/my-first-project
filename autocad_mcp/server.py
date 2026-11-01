"""AutoCAD MCP server. Every drawing tool takes an explicit backend: "com" (live AutoCAD or
Civil 3D) or "ezdxf" (DXF files, no AutoCAD). Tool descriptions are kept short and the input
schemas trimmed, because a small local model reads all of them at the start of every chat."""
import json
from typing import Literal

from mcp.server.fastmcp import FastMCP

import cad_workflow as workflow  # not "workflow": PyInstaller has a hook for a package of that name
import entities
import job
import settings
from backends import BACKENDS
from compare import compare, compare_text as text_report

mcp = FastMCP("autocad")

BackendName = Literal["com", "ezdxf"]
Direction = Literal["source_to_sample", "sample_to_source", "both"]
CopyMode = Literal["full", "part"]
RunMode = Literal["test", "full"]


def _backend(name: str):
    if name not in BACKENDS:
        raise ValueError(f"backend must be one of {sorted(BACKENDS)}, got {name!r}")
    return BACKENDS[name]


def _com_only(name: str, tool: str):
    if name != "com":
        raise ValueError(f"{tool} only works with backend='com'")
    return BACKENDS["com"]


def _records(b, path: str | None) -> list[dict]:
    if b.name == "com" and path:
        b.activate(path)
    return b.entities(path)


# Drawings


@mcp.tool()
def backend_status(backend: BackendName, path: str | None = None) -> dict:
    """com: the running AutoCAD/Civil 3D and its open drawings. ezdxf: a DXF file's version
    and object count (path required)."""
    return _backend(backend).status(path)


@mcp.tool()
def inspect_drawing(backend: BackendName, path: str | None = None) -> dict:
    """Summary of a drawing's model space: layers, blocks, styles, object counts per layer
    and type. ezdxf: path required. com: path picks an open drawing, else the active one."""
    return _backend(backend).inspect(path)


@mcp.tool()
def compare_drawings(backend: BackendName, source: str, sample: str,
                     direction: Direction = "source_to_sample") -> dict:
    """Differences between source and sample: exact counts, tag layers, xref-bound layers
    with their plain names, then layers, blocks, styles and object counts. Use the names
    exactly as given. com: both drawings must be open."""
    b = _backend(backend)
    return compare(b.inspect(source), b.inspect(sample), direction, settings.get("tag_layer_pattern"))


@mcp.tool()
def list_entities(backend: BackendName, path: str | None = None, layers: list[str] | None = None,
                  types: list[str] | None = None, text: str | None = None,
                  area: list[float] | None = None, limit: int = 100) -> dict:
    """Individual objects: handle, type, layer, position, plain text (TEXT, MTEXT, MULTILEADER,
    block attributes), polyline points. Filters: layers and types (exact names, or * ?
    wildcards), text (substring or wildcard), area [x0,y0,x1,y1]. Exact counts are given
    even when the list is cut at limit."""
    b = _backend(backend)
    picked = entities.select(_records(b, path), layers, types, text, area)
    return entities.listing(path or "active drawing", picked, limit)


@mcp.tool()
def compare_text(backend: BackendName, source: str, sample: str, layers: list[str] | None = None,
                 direction: Direction = "source_to_sample") -> dict:
    """Text of objects compared layer by layer: texts only in one drawing, with exact counts.
    layers: exact names or wildcards; default the tag layers (settings: tag_layer_pattern)."""
    b = _backend(backend)
    pattern = layers or [settings.get("tag_layer_pattern")]
    return text_report(entities.select(_records(b, source), pattern),
                       entities.select(_records(b, sample), pattern), direction)


@mcp.tool()
def save_as_dxf(backend: BackendName, path: str, close_after: bool = True) -> str:
    """com: save the active drawing as DXF R2018 for ezdxf; close_after closes it."""
    return _com_only(backend, "save_as_dxf").save_as_dxf(path, close_after)


@mcp.tool()
def open_in_autocad(backend: BackendName, path: str) -> str:
    """com: open or switch to a DWG/DXF file in AutoCAD."""
    return _com_only(backend, "open_in_autocad").open_drawing(path)


# LISP and commands (com only)


@mcp.tool()
def load_lisp(backend: BackendName, path: str) -> str:
    """com: load a .lsp file from the trusted LISP folder (path relative to it)."""
    return _com_only(backend, "load_lisp").load_lisp(job.routine_path(path))


@mcp.tool()
def run_lisp(backend: BackendName, expr: str) -> str:
    """com: evaluate a LISP expression; returns its result as text."""
    return _com_only(backend, "run_lisp").run_lisp(expr)


@mcp.tool()
def run_lisp_command(backend: BackendName, name: str) -> str:
    """com: run a loaded LISP command (c:NAME, give NAME); returns its result as text."""
    return _com_only(backend, "run_lisp_command").run_lisp_command(name)


@mcp.tool()
def run_command(backend: BackendName, command: str, answers: list[str] | None = None,
                path: str | None = None, timeout: float = 30.0) -> dict:
    """com: run a command and answer its prompts in order ("" = Enter). Cancelled with Esc if
    still waiting after timeout seconds. path picks the drawing."""
    com = _com_only(backend, "run_command")
    if path:
        com.activate(path)
    return com.run_command(command, answers, timeout)


# Job workflow. source_folder holds the source and sample, which are never edited; the tools
# write only into its work subfolder ("AI work").


@mcp.tool()
def make_test_copy(backend: BackendName, source_folder: str, source: str, mode: CopyMode = "full",
                   sample: str | None = None, layers: list[str] | None = None,
                   blocks: list[str] | None = None, types: list[str] | None = None,
                   margin: float = 0.1) -> dict:
    """Copy the source into the job's test folder, whole or a part (the given layers, blocks,
    types and their surroundings; without them, what the comparison with sample touches)."""
    return workflow.make_test_copy(_backend(backend), source_folder, source, mode, sample,
                                   layers, blocks, types, margin)


@mcp.tool()
def test_routine(backend: BackendName, source_folder: str, copy: str, routine: str, command: str,
                 answers: list[str] | None = None, sample: str | None = None,
                 run_twice: bool = True, timeout: float = 30.0, close_after: bool = False) -> dict:
    """com: test a routine from the trusted folder on a test copy: loads cleanly, runs
    unattended, does the job, changes nothing else, safe twice. Flags, never blocks."""
    return workflow.test_routine(_backend(backend), source_folder, copy, routine, command, answers,
                                 sample, run_twice, timeout, close_after)


@mcp.tool()
def save_recipe(source_folder: str, name: str, steps: list[dict], notes: str = "") -> dict:
    """Save a recipe as a draft (clears approval). Steps: {"lisp": CMD, "file": f.lsp,
    "answers": [...]}, {"command": C, "answers": [...]}, {"rename_layer": {"from", "to"}},
    {"delete": selector}, {"change_layer": {"select": selector, "to": L}}. Selector keys:
    layer, type, block. Use general rules, never handles."""
    return workflow.save_recipe(source_folder, name, steps, notes)


@mcp.tool()
def get_recipe(source_folder: str, name: str) -> dict:
    """A recipe with its status (draft/tested/approved) and routines changed since approval."""
    return workflow.get_recipe(source_folder, name)


@mcp.tool()
def run_recipe(backend: BackendName, source_folder: str, name: str, drawing: str,
               mode: RunMode = "test", sample: str | None = None, timeout: float = 60.0) -> dict:
    """com: run a recipe on a copy of drawing. test: always allowed. full: approved recipe
    with unchanged routines only; saves the shop drawing. Reports every step."""
    return workflow.run_recipe(_backend(backend), source_folder, name, drawing, mode, sample, timeout)


@mcp.tool()
def approve_recipe(source_folder: str, name: str, approved_by: str) -> dict:
    """Only when the user explicitly approves, with their name. Never on your own judgement."""
    return workflow.approve_recipe(source_folder, name, approved_by)


@mcp.tool()
def check_conventions(backend: BackendName, drawing: str, sample: str) -> dict:
    """Check a drawing against the sample's layers, styles and filled title-block attributes."""
    return workflow.check_conventions(_backend(backend), drawing, sample)


@mcp.tool()
def close_job(backend: BackendName, source_folder: str, name: str, shop_drawing: str,
              sample: str, closed_by: str, notes: str = "") -> dict:
    """Only when the user asks: final check and job summary, with their name."""
    return workflow.close_job(_backend(backend), source_folder, name, shop_drawing, sample,
                              closed_by, notes)


def _slim(schema):
    """Drop what a model doesn't need from an input schema: titles, "null" alternatives of
    optional arguments and null defaults. Arguments are still validated in full."""
    if isinstance(schema, list):
        return [_slim(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _slim(v) for k, v in schema.items() if k != "title"}
    alts = out.get("anyOf")
    if alts:
        kept = [a for a in alts if a != {"type": "null"}]
        if len(kept) == 1:
            out.pop("anyOf")
            out.update(kept[0])
    if "default" in out and out["default"] is None:
        out.pop("default")
    return out


def _compact(fn):
    """Return results as one line of JSON, once. By default they are pretty-printed and also
    sent a second time as structured data, which a slow local model reads in full."""
    def call(**kwargs):
        result = fn(**kwargs)
        return result if isinstance(result, str) else json.dumps(
            result, separators=(",", ":"), ensure_ascii=False, default=str)
    return call


for _tool in mcp._tool_manager.list_tools():
    _tool.parameters = _slim(_tool.parameters)
    _tool.description = " ".join((_tool.description or "").split())
    _tool.fn = _compact(_tool.fn)
    _tool.fn_metadata.output_schema = None


if __name__ == "__main__":
    mcp.run()
