"""Tests of the teaching module that need no AutoCAD: the TCAP log reader, the teaching
files and the recipe's teaching reference.

    python tests\\test_teach.py
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autocad_mcp"))

import teach  # noqa: E402
import cad_workflow as workflow  # noqa: E402

LOG = """\
HEADER;2026-11-16 10:00:00;D:/jobs/001/SOURCE.dwg;AutoCAD;2018;A;D:/jobs/001/recording
CMD;2026-11-16 10:00:05;LINE;STARTED;
CHG;2026-11-16 10:00:09;LINE;ADD;1A2;0. TAG HC;LINE;;
CMD;2026-11-16 10:00:09;LINE;ENDED;
SEL;2026-11-16 10:00:20;none;GRIP;1A2;0. TAG HC;LINE;;
CMD;2026-11-16 10:00:21;MOVE;STARTED;
SEL;2026-11-16 10:00:25;MOVE;SELECT;1A2;0. TAG HC;LINE;;
CHG;2026-11-16 10:00:25;MOVE;MOD;1A2;0. TAG HC;LINE;;
CMD;2026-11-16 10:00:25;MOVE;ENDED;
CMD;2026-11-16 10:00:30;QSAVE;STARTED;
SAVE;2026-11-16 10:00:31;QSAVE;D:/jobs/001/SOURCE.dwg;001.dwg
CMD;2026-11-16 10:00:31;QSAVE;ENDED;
CMD;2026-11-16 10:01:00;C:GTAGA;STARTED;
CHG;2026-11-16 10:01:02;C:GTAGA;MOD;2B3;0. TAG IL005;TEXT;;IL 60.225, a;b
CHG;2026-11-16 10:01:02;C:GTAGA;MOD;+3 more
CMD;2026-11-16 10:01:02;C:GTAGA;ENDED;
CMD;2026-11-16 10:01:10;ERASE;STARTED;
CHG;2026-11-16 10:01:12;ERASE;DEL;2B4;C-INW;TEXT;;old
ERROR;2026-11-16 10:01:12;copy;disk full
CMD;2026-11-16 10:01:12;ERASE;CANCELLED;
CMD;2026-11-16 10:01:20;QSAVE;STARTED;
SAVE;2026-11-16 10:01:21;QSAVE;D:/jobs/001/SOURCE.dwg;002.dwg
CMD;2026-11-16 10:01:21;QSAVE;ENDED;
CMD;2026-11-16 10:01:30;CIRCLE;STARTED;
something odd
FOOTER;2026-11-16 10:02:00;STOP;2;2;4;6
"""


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rec = os.path.join(self.tmp, "recording")
        os.makedirs(self.rec)
        with open(os.path.join(self.rec, teach.LOG_NAME), "w", encoding="ascii") as f:
            f.write(LOG)
        for name in ("001.dwg", "002.dwg", "notes.txt"):
            open(os.path.join(self.rec, name), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse(self):
        records = teach.parse_log(LOG)
        self.assertEqual(records[0]["kind"], "HEADER")
        self.assertEqual(records[0]["product"], "AutoCAD")
        more = [r for r in records if "more" in r]
        self.assertEqual(len(more), 1)
        self.assertEqual(more[0]["more"], 3)
        self.assertEqual(sum(1 for r in records if r["kind"] == "?"), 1)

    def test_steps(self):
        out = teach.read_recording(self.tmp)
        self.assertEqual(out["copies"], ["001.dwg", "002.dwg"])
        self.assertEqual(out["steps_total"], 3)
        self.assertEqual(out["unknown_lines"], 1)
        s1, s2, s3 = out["steps"]
        self.assertEqual(s1["copy"], "001.dwg")
        self.assertEqual(s1["commands"], ["LINE", "MOVE", "QSAVE"])
        self.assertEqual(s1["changed"], {"ADD": 1, "MOD": 1, "DEL": 0})
        self.assertEqual(s1["selected"], 2)
        self.assertEqual(s1["selected_objects"][0]["how"], "GRIP")
        self.assertEqual(s2["copy"], "002.dwg")
        self.assertEqual(s2["commands"], ["C:GTAGA", "ERASE (cancelled)", "QSAVE"])
        self.assertEqual(s2["changed"], {"ADD": 0, "MOD": 1, "DEL": 1})
        self.assertEqual(s2["errors"], 1)
        self.assertEqual(s2["layers_changed"], {"0. TAG IL005": 1, "C-INW": 1})
        self.assertIsNone(s3["copy"])
        self.assertEqual(s3["commands"], ["CIRCLE (unfinished)"])
        self.assertEqual(out["commands_used"]["QSAVE"], 2)
        self.assertEqual(out["footer"]["reason"], "STOP")

    def test_one_step_in_full(self):
        out = teach.read_recording(self.tmp, step=2)
        step = out["step"]
        self.assertEqual(step["changed_more"], 3)
        self.assertEqual(step["changed_total"], {"ADD": 0, "MOD": 1, "DEL": 1})
        self.assertEqual(step["changed"]["MOD"][0]["text"], "IL 60.225, a")
        self.assertEqual(step["errors"][0]["message"], "disk full")
        with self.assertRaises(ValueError):
            teach.read_recording(self.tmp, step=4)

    def test_absolute_folder(self):
        out = teach.read_recording(None, self.rec)
        self.assertEqual(out["steps_total"], 3)
        with self.assertRaises(FileNotFoundError):
            teach.read_recording(self.tmp, "nowhere")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = os.path.join(self.tmp, "job")
        os.makedirs(self.job)
        os.environ[teach.TEACH_DIR_ENV] = os.path.join(self.tmp, "shared")

    def tearDown(self):
        os.environ.pop(teach.TEACH_DIR_ENV, None)
        shutil.rmtree(self.tmp)

    def test_read_empty(self):
        out = teach.read()
        self.assertIsNone(out["glossary"])
        self.assertNotIn("task", out)
        out = teach.read(self.job)
        self.assertIsNone(out["task"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "shared")))

    def test_write_and_history(self):
        teach.write("glossary", "# Terms\nHC: house connection\n")
        teach.write("task", "# Task\nfill tags", self.job)
        teach.write("task", "# Task\nfill tags, corrected", self.job, author="user")
        out = teach.read(self.job)
        self.assertIn("house connection", out["glossary"])
        self.assertTrue(out["task"].endswith("corrected\n"))
        history = os.listdir(os.path.join(out["job_folder"], "history"))
        self.assertEqual(len(history), 3)  # model, replaced, user
        self.assertTrue(any(h.endswith("-user.md") for h in history))
        with self.assertRaises(ValueError):
            teach.write("rules", "x")  # a job file without a job
        with self.assertRaises(ValueError):
            teach.write("manual", "x", self.job)

    def test_recipe_teaching(self):
        steps = [{"command": "-PURGE", "answers": ["_ALL", "*", "_N"]}]
        out = workflow.save_recipe(self.job, "r1", steps, teaching=["task", "rules", "task"])
        self.assertEqual(out["status"], "draft")
        self.assertEqual(workflow.get_recipe(self.job, "r1")["teaching"], ["task", "rules"])
        with self.assertRaises(ValueError):
            workflow.save_recipe(self.job, "r2", steps, teaching=["notes"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
