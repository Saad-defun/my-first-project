# Civil Automation 3.00: project instructions

*Paste this into the Project instructions of **Civil Automation 3.00**. It is self-contained:
the chats in that project cannot see the AI to CAD conversation.*

---

## What this project is for

This project prepares AutoLISP routines so they can be run **unattended** by **AI to CAD**,
a tool that drives AutoCAD and Civil 3D (2018 to 2026) over COM. AI to CAD turns a **source**
drawing into a **shop drawing** by repeating the processing shown in a **sample** drawing
(the sample was made from the same source). Each step of that processing is a LISP routine,
an AutoCAD command or a simple rule, collected in a **recipe**.

Nobody sits at AutoCAD while a recipe runs. A routine that waits for a pick, opens a dialog
or asks a different number of questions each time stops the whole run. The job here is to
take routines from anywhere (the Civil Automations libraries, other sources, or new code)
and make an **automation version** of each one that never waits for a person.

The requirements here are different from the other Civil Automations projects. Change code
as needed to meet them.

## How AI to CAD runs a routine

1. It loads the `.lsp` file from AutoCAD's trusted LISP folder. It loads no other files.
2. It types the command name, then each answer from the recipe in order, each followed by
   Enter. An empty answer is a plain Enter.
3. It waits until no command is active, up to a time limit (60 s by default). If a command
   is still active after that, it presses Esc twice, marks the step failed and moves on.
4. It compares the drawing before and after (layers, blocks, styles, object counts per layer
   and type) with the sample, and with a second run of the same command.

A **direct call** step is planned: the recipe calls the core function with arguments, for
example `(MYCMD-core "WALL" 2.5)`, with no prompts at all. Until it exists, the automation
command with fixed answers (rule 3 below) is how a routine runs.

## Rules for an automation version

1. **Keep the original.** Don't change how the existing command behaves for a person at the
   keyboard. Add the automation version beside it.
2. **Core function.** Put the work in `(defun <CMD>-core (arg1 arg2 ...) ...)`: everything
   it needs comes in as arguments, it asks nothing, and it returns a short result string.
   The original command can then gather inputs and call the core.
3. **Automation command `c:<CMD>A`.** It asks for the core's arguments with simple prompts
   only (`getstring`, `getreal`, `getint`, `getkword`, or points typed as `x,y`), **always the
   same number of prompts in the same order**, then calls the core. No loops like "pick next
   point", and no prompt that appears only sometimes.
4. **No picking on screen.** Replace `entsel`, `nentsel`, `ssget` with picks and window
   selection by `ssget "_X"` with a filter list built from the arguments (layer, type, block
   name, colour). If the routine needs "the object", select it by a rule (for example the
   one block named X, or the text on layer Y).
5. **No dialogs or message boxes.** No DCL, no `alert`, no `getfiled`, no `initdia`. Report
   with `princ` instead. A message box blocks AutoCAD until someone clicks it.
6. **Leave AutoCAD as you found it.** Save every system variable you change (CMDECHO, OSMODE,
   CLAYER, ATTREQ, ATTDIA, FILEDIA, PICKSTYLE, TEXTSTYLE, DIMSTYLE and so on) and restore it
   at the end **and** in a local `*error*` handler. AI to CAD flags any system variable left
   changed.
7. **Safe to run twice.** Running the command a second time on the same drawing should change
   nothing (check before adding, renaming or creating). If a routine can't be like that by
   nature, say so in its Notes, because AI to CAD flags it.
8. **Commands inside LISP.** Use `(command "_.LAYER" ...)` with underscore and dot, so they
   work in any language and aren't affected by redefined commands. Never leave a command
   unfinished: finish every `command` call.
9. **Names.** A command must not be an AutoCAD command name and must not clash with a command
   already in the library. Prefix every helper with the command name (`<CMD>-...`), because
   AutoCAD keeps one set of function names for everything loaded.
10. **Versions.** It must work in AutoCAD 2018 (the oldest version in use) up to 2026. Don't
    use functions newer than AutoCAD 2018. Routines that need Civil 3D objects must say
    **Civil 3D only** in their header.
11. **Files.** One `.lsp` per routine, plain ASCII (no characters outside A-Z, digits and
    normal punctuation, because AutoCAD 2018 doesn't read UTF-8 reliably). Keep the
    attribution comments of borrowed code, and rename any borrowed helper you change.

## What to deliver for each routine

1. **`<CMD>.lsp`**: the original command (unchanged, or calling the core), the core function,
   the `c:<CMD>A` automation command, and the helpers.
2. **A recipe step** ready to paste into AI to CAD:

   ```yaml
   - lisp: MYCMDA
     file: mycmd.lsp
     answers: ["WALL", "2.5", "_Yes"]
     note: what this step does
   ```

3. **Notes**, in plain words:
   - what it changes (layers, blocks, text, dimensions…) and what it must not touch;
   - every prompt of `c:<CMD>A`, in order, with allowed values and an example answer;
   - which objects it selects, and the filter it uses;
   - whether it's safe to run twice;
   - Civil 3D only or not;
   - what was tested, on which AutoCAD version, and what was not.

## Test reports from AI to CAD

AI to CAD tests each routine on a copy of the source and saves a JSON report. When a report
is pasted here, fix the routine according to the failed check:

| Check | Fails when | Usual fix |
|---|---|---|
| `loads_cleanly` | load error, a helper name used by another file in the trusted folder, a command that replaces an AutoCAD command, or the command isn't defined in the file | fix the syntax, rename helpers with the command prefix, rename the command |
| `runs_unattended` | the command was still waiting at the time limit | remove picks, dialogs, loops and optional prompts (rules 3 to 5), or correct the answers |
| `does_the_job` | nothing changed towards the sample | check the selection filter and the arguments against the sample |
| `changes_nothing_else` | changes that the sample doesn't call for, or system variables left changed | narrow the selection, restore system variables (rule 6) |
| `safe_to_run_twice` | the second run changed the drawing again | check before adding or creating (rule 7) |

## Before starting a routine

- Search the Civil Automations library (its `README.md` and `code-*.md` files) for the routine
  and anything close to it, using several word forms (`txt`, `pl`, `blk`, `att`, `dim`…).
  Say what exists before writing code, and build on the real code.
- Read the routine's Notes in the library: they hold limits and values that must not be
  "improved".
- List every prompt, pick and dialog in the original. Each one becomes an argument of the core.

## Checklist before delivering

- [ ] Original command still behaves as before.
- [ ] `<CMD>-core` takes arguments and asks nothing.
- [ ] `c:<CMD>A` has a fixed number of simple prompts, always in the same order.
- [ ] No `entsel`, picked `ssget`, DCL, `alert` or `getfiled` anywhere on the automation path.
- [ ] System variables restored at the end and in `*error*`.
- [ ] A second run changes nothing (or the Notes say why it must).
- [ ] Command and helper names checked against the library and AutoCAD commands.
- [ ] Plain ASCII file, attribution kept.
- [ ] Recipe step and Notes delivered with the `.lsp`.
