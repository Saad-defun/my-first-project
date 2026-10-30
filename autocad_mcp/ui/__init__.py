"""Desktop UI for the AI to CAD workflow. It calls the same code as the MCP tools, so a job
can be worked on from the UI and from Claude interchangeably."""


def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AI to CAD")
    # A problem found before an action starts (e.g. no source folder chosen) is shown, not lost
    sys.excepthook = lambda kind, err, tb: QMessageBox.warning(None, "AI to CAD", str(err))
    win = MainWindow()
    win.show()
    return app.exec()
