"""Settings kept in settings.ini beside the exe (or beside this file when run from source),
so the program stays portable. Created with the defaults on first use.

    [autocad]
    lisp_dir    = AutoCAD's trusted LISP folder, the only place routines load from
    product     = AutoCAD | Civil 3D | any
    version     = 2018 ... 2026 | any
    save_format = 2018 | 2013 | 2010 | 2007 | 2004 | 2000   (shop drawings)
    timeout     = seconds a command may wait before it is cancelled
"""
import configparser
import os
import sys

SECTION = "autocad"
PRODUCTS = ("AutoCAD", "Civil 3D", "any")
VERSIONS = tuple(str(y) for y in range(2018, 2027)) + ("any",)
SAVE_FORMATS = ("2018", "2013", "2010", "2007", "2004", "2000")
DEFAULTS = {
    "lisp_dir": "",
    "product": "AutoCAD",
    "version": "2018",
    "save_format": "2018",
    "timeout": "60",
}


def path() -> str:
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    return os.path.join(base, "settings.ini")


def load() -> dict:
    cfg = configparser.ConfigParser()
    cfg[SECTION] = DEFAULTS
    if os.path.exists(path()):
        cfg.read(path(), encoding="utf-8")
    else:
        save(DEFAULTS)
    return dict(cfg[SECTION])


def save(values: dict) -> str:
    current = {**DEFAULTS, **{k: str(v) for k, v in values.items() if k in DEFAULTS}}
    errors = validate(current)
    if errors:
        raise ValueError("; ".join(errors))
    cfg = configparser.ConfigParser()
    cfg[SECTION] = current
    with open(path(), "w", encoding="utf-8") as f:
        cfg.write(f)
    return path()


def validate(values: dict) -> list[str]:
    errors = []
    if values["product"] not in PRODUCTS:
        errors.append(f"product must be one of {list(PRODUCTS)}")
    if values["version"] not in VERSIONS:
        errors.append("version must be 2018 to 2026, or any")
    if values["save_format"] not in SAVE_FORMATS:
        errors.append(f"save_format must be one of {list(SAVE_FORMATS)}")
    try:
        if float(values["timeout"]) <= 0:
            raise ValueError
    except ValueError:
        errors.append("timeout must be a positive number of seconds")
    return errors


def get(key: str) -> str:
    return load()[key]
