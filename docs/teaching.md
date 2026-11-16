# Teaching a task without explaining it

The expensive part of AI in CAD is not driving AutoCAD. It is telling the AI what to do and
what to leave alone, again on every job, in the AI's terms instead of the user's. GTAG took
weeks that way. The teaching tools in Build 3 replace explaining with three cheaper things:
a recording of the user doing the task once, an interview about what the recording leaves
open, and files that keep what was learned.

## The loop

1. **Record.** The user loads TCAP.lsp (from Civil Automation 3.00), runs TCAPSTART, does
   the task as usual, runs TCAPSTOP. Or the model starts and stops it with the `capture`
   tool. TCAP writes a `recording` folder beside the drawing: a numbered copy on every save
   and `capture-log.txt`. Screen video, if any, is the user's own.
2. **Reconstruct.** `read_recording` gives the steps, one per save: the commands run, the
   objects selected, the objects each command added, changed or deleted, with layer and
   text. `list_entities` and `compare_drawings` on the copies fill in what the log does not
   say. The model drafts the task page from this, in the user's words.
3. **Interview.** For each open question the model calls `point_at` to zoom AutoCAD to the
   object and asks "this one, by what rule?". The user answers in a line, or selects the
   right objects and the model reads them with `get_selection`.
4. **Keep.** `teach_write` saves the task page and the rules for the job, and the glossary
   and standards shared by every job. `teach_read` is read at the start of every task.
5. **Build.** The recipe is drafted from the task page and saved with `teaching` naming the
   files it came from. Each difference from the sample becomes a question, each answer a
   rule, and the recipe is fixed.

The measure of the whole thing: the second task needs fewer questions than the first.

## Files

```
<teach_dir>\                shared by every job (settings.ini: teach_dir;
    glossary.md             empty = "teaching" beside the exe or the source)
    standards.md
    history\
<job>\AI work\teach\        this job only
    task.md
    rules.md
    history\
```

Every write keeps the replaced version and the new one in `history\`, named with who wrote
it (model or user), so a draft and its correction sit side by side.

## Tools

| Tool | What it does |
|---|---|
| `get_selection` | The objects selected in AutoCAD right now (or the previous selection) |
| `point_at` | Zoom to objects by handle and grip-select them |
| `capture` | Start, status or stop TCAP in the active drawing; loads TCAP.lsp if needed |
| `read_recording` | A recording as steps, or one step in full |
| `teach_read` | The teaching files, missing ones as null |
| `teach_write` | Write one teaching file; earlier versions kept |
| `save_recipe` | Now takes `teaching`, the files the recipe was built from |

## Settings

- `teach_dir`: the shared teaching folder. Empty means `teaching` beside the exe (or beside
  the source when run from source). `AUTOCAD_MCP_TEACH_DIR` overrides it.
- `capture_file`: the TCAP file name in the trusted LISP folder, `TCAP.lsp` by default.

## Capture log

One record per line, fields separated by `;`, as Civil Automation 3.00 locked it for
TCAP v01.00. `read_recording` tolerates unknown lines and counts them.

```
HEADER;time;drawing path;product;version;user;folder path
CMD;time;name;STARTED/ENDED/CANCELLED/FAILED;parent
SEL;time;command or none;GRIP/SELECT;handle;layer;type;block;text
CHG;time;command or none;ADD/MOD/DEL;handle;layer;type;block;text
SAVE;time;command;saved file path;copy name
ERROR;time;where;message
FOOTER;time;STOP/CLOSE;copies;SEL objects;CHG objects;commands
```

## Not verified

Nothing here has run against a live AutoCAD: `get_selection`, `point_at` and `capture` are
COM and LISP calls checked by reading only. The log reader and the files are covered by
`tests\test_teach.py`.
