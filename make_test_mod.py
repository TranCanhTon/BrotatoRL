import json
import zipfile
from pathlib import Path

PROJECT = Path(r"D:\brotato-ai")
WORKSHOP = Path(r"D:\teasm\steamapps\workshop\content\1942280\3834398712")
MOD_ID = "Ton-BrotatoAI"

manifest = {
    "name": "BrotatoAI",
    "namespace": "Ton",
    "version_number": "0.2.0",
    "description": "Bridge between Brotato and a Python AI",
    "website_url": "",
    "dependencies": [],
    "extra": {
        "godot": {
            "incompatibilities": [],
            "authors": ["Ton"],
            "compatible_mod_loader_version": ["6.3.0"],
            "compatible_game_version": ["1.2.0.1"],
            "config_defaults": {},
        }
    },
}

mod_main = (PROJECT / "mod" / "mod_main.gd").read_text(encoding="utf-8")

zips = list(WORKSHOP.glob("*.zip"))
if len(zips) != 1:
    print("Expected exactly 1 zip in", WORKSHOP, "but found:", [z.name for z in zips])
    print("Paste this output to Claude.")
    raise SystemExit

zip_path = zips[0]
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr(f"mods-unpacked/{MOD_ID}/manifest.json", json.dumps(manifest, indent=4))
    z.writestr(f"mods-unpacked/{MOD_ID}/mod_main.gd", mod_main)

print("Mod written to", zip_path)
