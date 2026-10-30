"""Main window: one tab per workflow stage. Every action runs in the background, one at a
time, so AutoCAD is never driven from two places at once and the window stays responsive."""
import glob
import os
import sys

import yaml
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import job
import settings
import cad_workflow as workflow
from backends import BACKENDS
from compare import compare

DRAWING_TYPES = ("*.dwg", "*.dxf")
RECIPE_TEMPLATE = """\
# One step per item. Step types: lisp, command, rename_layer, delete, change_layer.
- lisp: MYCMD
  file: mycmd.lsp
  answers: ["_ALL", ""]
- command: "-PURGE"
  answers: ["_ALL", "*", "_N"]
- rename_layer: {from: OLD, to: NEW}
- delete: {layer: "TEMP*", type: TEXT}
- change_layer: {select: {type: DIMENSION}, to: DIM}
"""
PASS, FAIL, REVIEW = QColor("#2e7d32"), QColor("#c62828"), QColor("#ef6c00")


def _csv(text: str) -> list[str] | None:
    items = [x.strip() for x in text.split(",") if x.strip()]
    return items or None


def _drawings(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    found = {os.path.basename(p) for t in DRAWING_TYPES for p in glob.glob(os.path.join(folder, t))}
    return sorted(found, key=str.lower)


# Background work


class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class Worker(QRunnable):
    def __init__(self, wid: int, fn):
        super().__init__()
        self.wid, self.fn, self.signals = wid, fn, _Signals()
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.fn()
        except Exception as e:
            self.signals.failed.emit(self.wid, f"{type(e).__name__}: {e}")
            return
        self.signals.done.emit(self.wid, result)


# Result display


class ResultView(QTreeWidget):
    """Shows a report (nested dicts and lists) as a tree, pass in green, fail in red."""

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["Item", "Value"])
        self.setColumnWidth(0, 320)
        self.setAlternatingRowColors(True)

    def show_result(self, data):
        self.clear()
        self._add(self.invisibleRootItem(), data)
        self.expandToDepth(1)

    def show_error(self, message: str):
        self.clear()
        item = QTreeWidgetItem(["Error", message])
        item.setForeground(1, QBrush(FAIL))
        self.addTopLevelItem(item)

    def _add(self, parent, data):
        if isinstance(data, dict):
            for k, v in data.items():
                self._child(parent, str(k), v)
        elif isinstance(data, (list, tuple)):
            for i, v in enumerate(data, 1):
                self._child(parent, str(i), v)
        else:
            parent.addChild(QTreeWidgetItem(["", str(data)]))

    def _child(self, parent, key: str, value):
        if isinstance(value, (list, tuple)) and all(not isinstance(v, (dict, list)) for v in value):
            item = QTreeWidgetItem([key, ", ".join(map(str, value)) if value else "(none)"])
        elif isinstance(value, (dict, list, tuple)):
            item = QTreeWidgetItem([key, "(empty)" if not value else ""])
            self._add(item, value)
        else:
            item = QTreeWidgetItem([key, str(value)])
            color = {True: PASS, False: FAIL}.get(value) if key == "pass" else None
            if key == "result":
                color = {"pass": PASS, "flagged": FAIL, "needs review": REVIEW}.get(value)
            if color:
                item.setForeground(1, QBrush(color))
        parent.addChild(item)


# Pages


class Page(QWidget):
    """A workflow tab. run() does the work in the background and shows the result."""

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        self.view = ResultView()
        self._pending = {}

    def refresh(self):
        """Called when the tab is shown, to reload lists that may have changed."""

    def run(self, label: str, fn, button: QPushButton, after=None):
        wid = self.main.next_id()
        worker = Worker(wid, fn)
        worker.signals.done.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        self._pending[wid] = (worker, button, after)
        button.setEnabled(False)
        self.main.status(f"Working: {label} …")
        self.main.pool.start(worker)

    def _finish(self, wid: int):
        _, button, after = self._pending.pop(wid)
        button.setEnabled(True)
        self.main.status("Ready")
        return after

    def _on_done(self, wid: int, result):
        after = self._finish(wid)
        self.view.show_result(result)
        if after:
            after(result)

    def _on_failed(self, wid: int, message: str):
        self._finish(wid)
        self.view.show_error(message)
        self.main.status("Failed: " + message)

    # Shortcuts to the job chosen on the Job tab

    def backend(self):
        return BACKENDS[self.main.job.backend_box.currentText()]

    def folder(self) -> str:
        folder = self.main.job.folder_edit.text().strip()
        if not folder:
            raise ValueError("choose the source folder on the Job tab first")
        return folder

    def source(self) -> str:
        return self._drawing(self.main.job.source_box, "source")

    def sample(self) -> str:
        return self._drawing(self.main.job.sample_box, "sample")

    def _drawing(self, combo: QComboBox, what: str) -> str:
        name = combo.currentText().strip()
        if not name:
            raise ValueError(f"choose the {what} drawing on the Job tab first")
        return os.path.join(self.folder(), name)

    def timeout(self) -> float:
        return float(settings.get("timeout"))

    def recipe_names(self) -> list[str]:
        try:
            folder = job.sub(self.folder(), "recipe")
        except Exception:
            return []
        return sorted(os.path.splitext(f)[0] for f in os.listdir(folder) if f.endswith(".yaml"))

    def fill(self, combo: QComboBox, items: list[str]):
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if current in items:
            combo.setCurrentText(current)
        combo.blockSignals(False)


class JobPage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("folder holding the source and sample drawings")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.folder_edit.editingFinished.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit)
        row.addWidget(browse)
        self.source_box, self.sample_box = QComboBox(), QComboBox()
        self.backend_box = QComboBox()
        self.backend_box.addItems(sorted(BACKENDS))
        self.check = QPushButton("Check connection")
        self.check.clicked.connect(self._check)

        form = QFormLayout()
        form.addRow("Source folder", row)
        form.addRow("Source drawing", self.source_box)
        form.addRow("Sample drawing", self.sample_box)
        form.addRow("Backend", self.backend_box)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("com works on drawings open in AutoCAD / Civil 3D; ezdxf works on DXF files "
                                "directly. LISP, tests and recipe runs need com."))
        layout.addWidget(self.check)
        layout.addWidget(self.view)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Source folder", self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)
            self.refresh()

    def refresh(self):
        names = _drawings(self.folder_edit.text().strip())
        self.fill(self.source_box, names)
        self.fill(self.sample_box, names)
        for combo, word in ((self.source_box, "source"), (self.sample_box, "sample")):
            match = next((n for n in names if word in n.lower()), None)
            if match and combo.currentIndex() <= 0:
                combo.setCurrentText(match)

    def _check(self):
        b = self.backend()
        path = None
        if b.name == "ezdxf":
            path = self.source() if self.source_box.currentText() else None
            if not path:
                self.view.show_error("ezdxf needs a source drawing (a DXF) to check")
                return
        self.run("checking connection", lambda: b.status(path), self.check)


class ComparePage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.direction = QComboBox()
        self.direction.addItems(["source_to_sample", "sample_to_source", "both"])
        self.go = QPushButton("Compare source and sample")
        self.go.clicked.connect(self._compare)
        form = QFormLayout()
        form.addRow("Direction", self.direction)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.go)
        layout.addWidget(self.view)

    def _compare(self):
        b, src, smp, direction = self.backend(), self.source(), self.sample(), self.direction.currentText()
        self.run("comparing", lambda: compare(workflow._summary(b, src), workflow._summary(b, smp), direction),
                 self.go)


class TestPage(Page):
    def __init__(self, main):
        super().__init__(main)
        # Test copy
        self.mode = QComboBox()
        self.mode.addItems(["full", "part"])
        self.layers, self.blocks, self.types = QLineEdit(), QLineEdit(), QLineEdit()
        for w in (self.layers, self.blocks, self.types):
            w.setPlaceholderText("comma separated, wildcards allowed; empty = from the comparison")
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 5)
        self.margin.setSingleStep(0.05)
        self.margin.setValue(0.1)
        self.make = QPushButton("Make test copy")
        self.make.clicked.connect(self._make_copy)
        copy_box = QGroupBox("1. Test copy of the source")
        f1 = QFormLayout(copy_box)
        f1.addRow("Copy", self.mode)
        f1.addRow("Layers (part)", self.layers)
        f1.addRow("Blocks (part)", self.blocks)
        f1.addRow("Types (part)", self.types)
        f1.addRow("Margin (part)", self.margin)
        f1.addRow(self.make)

        # Routine test
        self.copy = QComboBox()
        self.routine = QComboBox()
        self.routine.setEditable(True)
        self.command = QLineEdit()
        self.command.setPlaceholderText("command name without c:")
        self.answers = QPlainTextEdit()
        self.answers.setPlaceholderText("one answer per line, in prompt order; an empty line is Enter")
        self.answers.setMaximumHeight(90)
        self.use_sample = QCheckBox("Judge against the sample")
        self.use_sample.setChecked(True)
        self.twice = QCheckBox("Run twice")
        self.twice.setChecked(True)
        self.close_after = QCheckBox("Close the copy afterwards")
        self.go = QPushButton("Test routine")
        self.go.clicked.connect(self._test)
        test_box = QGroupBox("2. Test a routine from the trusted LISP folder")
        f2 = QFormLayout(test_box)
        f2.addRow("Test copy", self.copy)
        f2.addRow("Routine file", self.routine)
        f2.addRow("Command", self.command)
        f2.addRow("Answers", self.answers)
        opts = QHBoxLayout()
        for w in (self.use_sample, self.twice, self.close_after):
            opts.addWidget(w)
        f2.addRow(opts)
        f2.addRow(self.go)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(copy_box)
        lv.addWidget(test_box)
        lv.addStretch()
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)

    def refresh(self):
        try:
            self.fill(self.copy, _drawings(job.sub(self.folder(), "test")))
        except Exception:
            self.fill(self.copy, [])
        try:
            base = job.lisp_dir()
            files = [os.path.relpath(p, base) for p in glob.glob(os.path.join(base, "**", "*.lsp"), recursive=True)]
        except Exception:
            files = []
        self.fill(self.routine, sorted(files, key=str.lower))

    def _make_copy(self):
        b, folder, src = self.backend(), self.folder(), self.source()
        part = self.mode.currentText() == "part"
        layers, blocks, types = (_csv(w.text()) for w in (self.layers, self.blocks, self.types))
        sample = self.sample() if part and not (layers or blocks or types) else None
        margin = self.margin.value()
        self.run("making a test copy",
                 lambda: workflow.make_test_copy(b, folder, src, self.mode.currentText(), sample,
                                                 layers, blocks, types, margin),
                 self.make, after=self._copied)

    def _copied(self, result):
        self.refresh()
        self.copy.setCurrentText(os.path.basename(result["copy"]))

    def _test(self):
        b, folder = self.backend(), self.folder()
        if not self.copy.currentText():
            self.view.show_error("make a test copy first")
            return
        copy = os.path.join(job.sub(folder, "test"), self.copy.currentText())
        sample = self.sample() if self.use_sample.isChecked() else None
        routine, command = self.routine.currentText().strip(), self.command.text().strip()
        answers = self.answers.toPlainText().splitlines() or None
        twice, close_after, timeout = self.twice.isChecked(), self.close_after.isChecked(), self.timeout()
        self.run(f"testing {command}",
                 lambda: workflow.test_routine(b, folder, copy, routine, command, answers, sample,
                                               twice, timeout, close_after),
                 self.go)


class RecipePage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.activated.connect(lambda _: self._load())
        load = QPushButton("Load")
        load.clicked.connect(self._load)
        self.notes = QLineEdit()
        self.steps = QPlainTextEdit(RECIPE_TEMPLATE)
        self.steps.setStyleSheet("font-family: Consolas, monospace;")
        self.save = QPushButton("Save as draft")
        self.save.clicked.connect(self._save)
        self.drawing = QComboBox()
        self.go = QPushButton("Test-run on a copy")
        self.go.clicked.connect(self._test_run)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recipe"))
        top.addWidget(self.name, 1)
        top.addWidget(load)
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Run on"))
        run_row.addWidget(self.drawing, 1)
        run_row.addWidget(self.go)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addLayout(top)
        lv.addWidget(QLabel("Notes"))
        lv.addWidget(self.notes)
        lv.addWidget(QLabel("Steps (YAML). Saving sets the recipe back to draft."))
        lv.addWidget(self.steps, 1)
        lv.addWidget(self.save)
        lv.addLayout(run_row)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)

    def refresh(self):
        self.fill(self.name, self.recipe_names())
        try:
            folder = self.folder()
            items = _drawings(folder) + [f"test/{n}" for n in _drawings(job.sub(folder, "test"))]
        except Exception:
            items = []
        self.fill(self.drawing, items)
        src = self.main.job.source_box.currentText()
        if src in items and self.drawing.currentIndex() <= 0:
            self.drawing.setCurrentText(src)

    def _load(self):
        try:
            data = workflow.get_recipe(self.folder(), self.name.currentText().strip())
        except Exception as e:
            self.view.show_error(str(e))
            return
        self.notes.setText(data.get("notes", ""))
        self.steps.setPlainText(yaml.safe_dump(data["steps"], sort_keys=False, allow_unicode=True))
        self.view.show_result({k: v for k, v in data.items() if k != "steps"})

    def _save(self):
        try:
            steps = yaml.safe_load(self.steps.toPlainText())
            result = workflow.save_recipe(self.folder(), self.name.currentText().strip(), steps,
                                          self.notes.text())
        except Exception as e:
            self.view.show_error(str(e))
            return
        self.view.show_result(result)
        self.refresh()

    def _test_run(self):
        b, folder, name = self.backend(), self.folder(), self.name.currentText().strip()
        drawing, sample, timeout = self.drawing.currentText(), self.sample(), self.timeout()
        self.run(f"test-running {name}",
                 lambda: workflow.run_recipe(b, folder, name, drawing, "test", sample, timeout), self.go)


class ApprovePage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.name = QComboBox()
        self.name.activated.connect(lambda _: self._show())
        show = QPushButton("Show status")
        show.clicked.connect(self._show)
        self.by = QLineEdit()
        self.by.setPlaceholderText("your name")
        self.go = QPushButton("Approve recipe")
        self.go.clicked.connect(self._approve)
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(show)
        form = QFormLayout()
        form.addRow("Recipe", row)
        form.addRow("Approved by", self.by)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Approve only after reviewing the test run in AutoCAD. Approval locks the "
                                "routines' versions; a full run is refused if any of them changes."))
        layout.addWidget(self.go)
        layout.addWidget(self.view)

    def refresh(self):
        self.fill(self.name, self.recipe_names())

    def _show(self):
        try:
            data = workflow.get_recipe(self.folder(), self.name.currentText())
        except Exception as e:
            self.view.show_error(str(e))
            return
        self.view.show_result(data)

    def _approve(self):
        name, by = self.name.currentText(), self.by.text().strip()
        if not by:
            self.view.show_error("enter your name")
            return
        if QMessageBox.question(self, "Approve", f"Approve recipe '{name}' as {by}?") != QMessageBox.Yes:
            return
        try:
            self.view.show_result(workflow.approve_recipe(self.folder(), name, by))
        except Exception as e:
            self.view.show_error(str(e))


class RunPage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.name = QComboBox()
        self.drawing = QComboBox()
        self.go = QPushButton("Run on the full drawing")
        self.go.clicked.connect(self._run)
        form = QFormLayout()
        form.addRow("Approved recipe", self.name)
        form.addRow("Drawing", self.drawing)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Works on a copy; the shop drawing is saved in shop/ in the format set in "
                                "Settings and left open in AutoCAD for review."))
        layout.addWidget(self.go)
        layout.addWidget(self.view)

    def refresh(self):
        self.fill(self.name, self.recipe_names())
        try:
            self.fill(self.drawing, _drawings(self.folder()))
        except Exception:
            self.fill(self.drawing, [])
        src = self.main.job.source_box.currentText()
        if src and self.drawing.currentIndex() <= 0:
            self.drawing.setCurrentText(src)

    def _run(self):
        b, folder, name = self.backend(), self.folder(), self.name.currentText()
        drawing, sample, timeout = self.drawing.currentText(), self.sample(), self.timeout()
        if QMessageBox.question(self, "Full run", f"Run '{name}' on {drawing}?") != QMessageBox.Yes:
            return
        self.run(f"running {name}",
                 lambda: workflow.run_recipe(b, folder, name, drawing, "full", sample, timeout), self.go)


class ClosePage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.name = QComboBox()
        self.shop = QComboBox()
        self.by = QLineEdit()
        self.by.setPlaceholderText("your name")
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(80)
        self.check = QPushButton("Check conventions")
        self.check.clicked.connect(self._check)
        self.go = QPushButton("Close job")
        self.go.clicked.connect(self._close)
        form = QFormLayout()
        form.addRow("Recipe", self.name)
        form.addRow("Shop drawing", self.shop)
        form.addRow("Closed by", self.by)
        form.addRow("Notes", self.notes)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check)
        buttons.addWidget(self.go)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.view)

    def refresh(self):
        self.fill(self.name, self.recipe_names())
        try:
            self.fill(self.shop, _drawings(job.sub(self.folder(), "shop")))
        except Exception:
            self.fill(self.shop, [])

    def _shop_path(self) -> str:
        if not self.shop.currentText():
            raise ValueError("no shop drawing in shop/ yet; do a full run first")
        return os.path.join(job.sub(self.folder(), "shop"), self.shop.currentText())

    def _check(self):
        b, shop, sample = self.backend(), self._shop_path(), self.sample()
        self.run("checking conventions", lambda: workflow.check_conventions(b, shop, sample), self.check)

    def _close(self):
        by = self.by.text().strip()
        if not by:
            self.view.show_error("enter your name")
            return
        b, folder, name, shop, sample = self.backend(), self.folder(), self.name.currentText(), \
            self._shop_path(), self.sample()
        notes = self.notes.toPlainText()
        if QMessageBox.question(self, "Close job", f"Close the job with recipe '{name}'?") != QMessageBox.Yes:
            return
        self.run("closing the job",
                 lambda: workflow.close_job(b, folder, name, shop, sample, by, notes), self.go)


class SettingsPage(Page):
    def __init__(self, main):
        super().__init__(main)
        self.lisp_dir = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.lisp_dir)
        row.addWidget(browse)
        self.product, self.version, self.save_format = QComboBox(), QComboBox(), QComboBox()
        self.product.addItems(settings.PRODUCTS)
        self.version.addItems(settings.VERSIONS)
        self.save_format.addItems(settings.SAVE_FORMATS)
        self.timeout_box = QDoubleSpinBox()
        self.timeout_box.setRange(1, 3600)
        self.timeout_box.setSuffix(" s")
        self.go = QPushButton("Save settings")
        self.go.clicked.connect(self._save)
        form = QFormLayout()
        form.addRow("Trusted LISP folder", row)
        form.addRow("Product", self.product)
        form.addRow("Version", self.version)
        form.addRow("Shop drawing format", self.save_format)
        form.addRow("Command time limit", self.timeout_box)
        self.mcp = QPlainTextEdit()
        self.mcp.setReadOnly(True)
        self.mcp.setMaximumHeight(140)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.go)
        layout.addWidget(QLabel(f"Settings file: {settings.path()}"))
        layout.addWidget(QLabel("To use this program as the MCP server for Claude Desktop / Claude Code:"))
        layout.addWidget(self.mcp)
        layout.addWidget(self.view)
        self.refresh()

    def refresh(self):
        s = settings.load()
        self.lisp_dir.setText(s["lisp_dir"])
        self.product.setCurrentText(s["product"])
        self.version.setCurrentText(s["version"])
        self.save_format.setCurrentText(s["save_format"])
        self.timeout_box.setValue(float(s["timeout"]))
        exe = sys.executable if getattr(sys, "frozen", False) else f"{sys.executable} {os.path.abspath(sys.argv[0])}"
        cmd = exe.replace("\\", "\\\\")
        self.mcp.setPlainText(
            "Claude Code:\n"
            f"  claude mcp add autocad -- {exe} --mcp\n\n"
            "Claude Desktop (claude_desktop_config.json):\n"
            f'  {{"mcpServers": {{"autocad": {{"command": "{cmd}", "args": ["--mcp"]}}}}}}'
        )

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Trusted LISP folder", self.lisp_dir.text())
        if d:
            self.lisp_dir.setText(d)

    def _save(self):
        try:
            path = settings.save({
                "lisp_dir": self.lisp_dir.text().strip(),
                "product": self.product.currentText(),
                "version": self.version.currentText(),
                "save_format": self.save_format.currentText(),
                "timeout": self.timeout_box.value(),
            })
        except Exception as e:
            self.view.show_error(str(e))
            return
        self.view.show_result({"saved": path, **settings.load()})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI to CAD")
        self.resize(1200, 780)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)  # one AutoCAD action at a time
        self._ids = 0
        self.job = JobPage(self)
        self.tabs = QTabWidget()
        pages = [("Job", self.job), ("Compare", ComparePage(self)), ("Test", TestPage(self)),
                 ("Recipe", RecipePage(self)), ("Approve", ApprovePage(self)), ("Run", RunPage(self)),
                 ("Close", ClosePage(self)), ("Settings", SettingsPage(self))]
        for title, page in pages:
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(lambda i: self._safe_refresh(self.tabs.widget(i)))
        self.setCentralWidget(self.tabs)
        self.status("Ready")

    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    def status(self, text: str):
        self.statusBar().showMessage(text)

    def _safe_refresh(self, page):
        try:
            page.refresh()
        except Exception as e:
            self.status(f"Could not refresh: {e}")

    def closeEvent(self, event):
        if self.pool.activeThreadCount():
            if QMessageBox.question(self, "Quit", "An action is still running. Quit anyway?") != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
