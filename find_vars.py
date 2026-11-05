from pathlib import Path

SOURCE = Path(r"D:\brotato-ai\brotato-source")
REPORT = Path(r"D:\brotato-ai\find_report.txt")
MAX_HITS = 30

KEYWORDS = [
    "class_name Player", "current_health", "max_hp", "health",
    "current_wave", "wave_timer", "WaveTimer", "time_left",
    "gold", "materials", "consumable",
    "get_nodes_in_group", "add_to_group",
    "projectile", "EntitySpawner", "enemies",
    "time_scale", "change_scene", "on_player_died", "game_over",
]

files = sorted(SOURCE.rglob("*.gd"))
lines_out = [f"{len(files)} .gd files found\n"]

lines_out.append("=== FILES IN entities AND singletons ===")
for f in files:
    rel = f.relative_to(SOURCE).as_posix()
    if rel.startswith(("entities/", "singletons/")) or "/" not in rel:
        lines_out.append(rel)

for word in KEYWORDS:
    lines_out.append(f"\n=== {word} ===")
    hits = 0
    for f in files:
        rel = f.relative_to(SOURCE).as_posix()
        if rel.startswith("addons/"):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if word in line:
                lines_out.append(f"{rel}:{n}: {line.strip()[:150]}")
                hits += 1
                if hits >= MAX_HITS:
                    break
        if hits >= MAX_HITS:
            lines_out.append("(more hits cut off)")
            break

REPORT.write_text("\n".join(lines_out), encoding="utf-8")
print("Report saved to", REPORT, "with", len(lines_out), "lines")