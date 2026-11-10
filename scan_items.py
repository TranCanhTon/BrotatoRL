"""Reads every item in brotato-source and writes items_report.json: for each item its id, tier, price,
tags, and every effect (the effect type, its key and value). Only reads files, changes nothing.

Usage:  python scan_items.py
"""

import json
import os
import re

SOURCE = "brotato-source"
ITEMS_DIR = os.path.join(SOURCE, "items", "all")
OUT = "items_report.json"


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def res_to_path(res):
    """res://items/... -> brotato-source/items/..."""
    return os.path.join(SOURCE, *res[len("res://"):].split("/"))


def field(text, name):
    """Value of 'name = ...' in the [resource] part of a .tres file, as text (None if missing)."""
    m = re.search(r"^" + re.escape(name) + r" = (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def ext_resources(text):
    """{id: path} of the [ext_resource ...] lines."""
    out = {}
    for m in re.finditer(r'\[ext_resource path="([^"]+)" type="[^"]+" id=(\d+)\]', text):
        out[m.group(2)] = m.group(1)
    return out


def read_effect(res):
    path = res_to_path(res)
    if not os.path.exists(path):
        return {"file": res, "missing": True}
    text = read(path)
    ext = ext_resources(text)
    script_id = re.search(r"^script = ExtResource\( (\d+) \)", text, re.M)
    script = ext.get(script_id.group(1), "") if script_id else ""
    effect = {"script": script.replace("res://", "")}
    for name in ("key", "custom_key", "text_key", "value", "effect_sign", "chance"):
        v = field(text, name)
        if v is not None:
            effect[name] = v.strip('"')
    return effect


def main():
    items = []
    for root, _dirs, files in os.walk(ITEMS_DIR):
        for name in files:
            if not name.endswith(".tres"):
                continue
            path = os.path.join(root, name)
            text = read(path)
            my_id = field(text, "my_id")
            effects_line = field(text, "effects")
            if my_id is None or effects_line is None or not my_id.strip('"').startswith("item_"):
                continue
            ext = ext_resources(text)
            effect_ids = re.findall(r"ExtResource\( (\d+) \)", effects_line)
            items.append({
                "id": my_id.strip('"'),
                "pack": os.path.relpath(path, ITEMS_DIR).split(os.sep)[0],
                "tier": field(text, "tier"),
                "price": field(text, "value"),
                "max_nb": field(text, "max_nb"),
                "can_be_looted": field(text, "can_be_looted"),
                "tags": field(text, "tags"),
                "effects": [read_effect(ext[i]) for i in effect_ids if i in ext],
            })
    items.sort(key=lambda it: it["id"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    print(f"{len(items)} items written to {OUT}")


if __name__ == "__main__":
    main()
