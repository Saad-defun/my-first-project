"""AutoCAD MCP server. Every tool takes an explicit backend: "com" or "ezdxf"."""
from typing import Literal

from mcp.server.fastmcp import FastMCP

import job
import cad_workflow as workflow  # not "workflow": PyInstaller has a hook for a package of that name
from backends import BACKENDS
from compare import compare

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


@mcp.tool()
def backend_status(backend: BackendName, path: str | None = None) -> dict:
    """Check a backend is usable. com: reports the running AutoCAD or Civil 3D (product and
    version must match settings.ini) and its open drawings.
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
    """Load a .lsp file from AutoCAD's trusted LISP folder (lisp_dir in settings.ini) into the
    active drawing. `path` is relative to that folder; files outside it are refused. com only."""
    return _com_only(backend, "load_lisp").load_lisp(job.routine_path(path))


@mcp.tool()
def run_lisp(backend: BackendName, expr: str) -> str:
    """Evaluate a LISP expression in AutoCAD and return its result as text. com only."""
    return _com_only(backend, "run_lisp").run_lisp(expr)


@mcp.tool()
def run_lisp_command(backend: BackendName, name: str) -> str:
    """Run a loaded LISP command (defun c:NAME) and return its result as text. com only."""
    return _com_only(backend, "run_lisp_command").run_lisp_command(name)


@mcp.tool()
def run_command(backend: BackendName, command: str, answers: list[str] | None = None,
                path: str | None = None, timeout: float = 30.0) -> dict:
    """Run an AutoCAD command (or a loaded LISP command by name) and answer its prompts in
    order; "" is Enter. `path` picks the drawing (opened if needed), else the active one.
    Cancels and raises if it still waits for input after `timeout` seconds. com only."""
    com = _com_only(backend, "run_command")
    if path:
        com.activate(path)
    return com.run_command(command, answers, timeout)


# Job workflow. source_folder holds the source and sample drawings; drawing paths may be
# relative to it. The source and sample are never edited: all work happens on copies.


@mcp.tool()
def make_test_copy(backend: BackendName, source_folder: str, source: str, mode: CopyMode = "full",
                   sample: str | None = None, layers: list[str] | None = None,
                   blocks: list[str] | None = None, types: list[str] | None = None,
                   margin: float = 0.1) -> dict:
    """Copy the source into <source folder>/test/, whole (mode="full") or cut to a part.
    A part keeps the given layers/blocks/types (wildcards allowed) plus everything in their
    area grown by `margin`. With none given, a part covers what the source → sample
    comparison says the processing touches (needs `sample`). ezdxf parts need a DXF."""
    return workflow.make_test_copy(_backend(backend), source_folder, source, mode, sample,
                                   layers, blocks, types, margin)


@mcp.tool()
def test_routine(backend: BackendName, source_folder: str, copy: str, routine: str, command: str,
                 answers: list[str] | None = None, sample: str | None = None,
                 run_twice: bool = True, timeout: float = 30.0, close_after: bool = False) -> dict:
    """Test one routine from the trusted folder on a test copy (in test/). Checks: loads
    cleanly (no load error, no helper or command name clashes), runs unattended, does the
    job (changes move the copy towards `sample`), changes nothing else (incl. system
    variables), safe to run twice. Failures are flagged, not blocking. Saves a report in
    test/ and records the routine in lisp/. `command` is without c:. com only."""
    return workflow.test_routine(_backend(backend), source_folder, copy, routine, command, answers,
                                 sample, run_twice, timeout, close_after)


@mcp.tool()
def save_recipe(source_folder: str, name: str, steps: list[dict], notes: str = "") -> dict:
    """Save a recipe as a draft in <source folder>/recipe/<name>.yaml (clears any approval).
    Each step is one of:
      {"lisp": "MYCMD", "file": "mycmd.lsp", "answers": ["_ALL", ""]}
      {"command": "-PURGE", "answers": ["_ALL", "*", "_N"]}
      {"rename_layer": {"from": "OLD", "to": "NEW"}}
      {"delete": {"layer": "TEMP*", "type": "TEXT"}}
      {"change_layer": {"select": {"type": "DIMENSION"}, "to": "DIM"}}
    Selectors use layer / type / block, each a wildcard or list. Use general rules, not
    entity handles, so the recipe works on new sources too."""
    return workflow.save_recipe(source_folder, name, steps, notes)


@mcp.tool()
def get_recipe(source_folder: str, name: str) -> dict:
    """Read a recipe, its status (draft / tested / approved) and any routines changed since approval."""
    return workflow.get_recipe(source_folder, name)


@mcp.tool()
def run_recipe(backend: BackendName, source_folder: str, name: str, drawing: str,
               mode: RunMode = "test", sample: str | None = None, timeout: float = 60.0) -> dict:
    """Run a recipe on a copy of `drawing` (never on the drawing itself).
    test: copy in test/, always allowed; marks a draft recipe tested.
    full: needs an approved recipe whose routines are unchanged since approval; saves the
    shop drawing as DWG 2018 in shop/. With `sample`, reports what still differs and what
    the recipe does not cover. Every step's outcome and its routine's test result are
    reported; failures are flagged, not blocking. com only."""
    return workflow.run_recipe(_backend(backend), source_folder, name, drawing, mode, sample, timeout)


@mcp.tool()
def approve_recipe(source_folder: str, name: str, approved_by: str) -> dict:
    """Approve a tested recipe and lock its routines' hashes so a full run can go ahead.
    Call this ONLY when the user has explicitly said they approve this recipe, and pass
    their name. Never approve on your own judgement."""
    return workflow.approve_recipe(source_folder, name, approved_by)


@mcp.tool()
def check_conventions(backend: BackendName, drawing: str, sample: str) -> dict:
    """Check a drawing (e.g. a shop drawing) against the sample's conventions: layers the
    sample uses exist, no entities on layers the sample lacks, layer properties and text/dim
    styles match, title-block attributes are filled. com: both are opened in AutoCAD."""
    return workflow.check_conventions(_backend(backend), drawing, sample)


@mcp.tool()
def close_job(backend: BackendName, source_folder: str, name: str, shop_drawing: str,
              sample: str, closed_by: str, notes: str = "") -> dict:
    """Final step: run the conventions check on the shop drawing and write job-summary.json
    and job-summary.md in the source folder (recipe, routine versions, checks, open flags).
    Call only when the user asks to close the job, and pass their name."""
    return workflow.close_job(_backend(backend), source_folder, name, shop_drawing, sample,
                              closed_by, notes)


if __name__ == "__main__":
    mcp.run()
