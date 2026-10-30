"""Entry point of AItoCAD.exe.

    AItoCAD.exe          opens the UI
    AItoCAD.exe --mcp    runs the MCP server (stdio) for Claude Desktop and Claude Code

The exe is built as a console program because the MCP server talks over stdin/stdout.
When the UI is started by double-clicking, the console window that comes with it is hidden.
"""
import sys


def _hide_own_console():
    """Hide the console only if it was opened just for this program (not a terminal it was run from)."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        processes = (ctypes.c_uint * 8)()
        # A one-file exe runs as two processes (unpacker + program), both on the console
        own = 2 if getattr(sys, "frozen", False) else 1
        if kernel32.GetConsoleProcessList(processes, 8) <= own:
            ctypes.windll.user32.ShowWindow(kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass


def main() -> int:
    if "--mcp" in sys.argv[1:]:
        from server import mcp

        mcp.run()
        return 0
    _hide_own_console()
    from ui import main as ui_main

    return ui_main()


if __name__ == "__main__":
    sys.exit(main())
