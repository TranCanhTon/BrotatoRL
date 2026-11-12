"""What the shop brain prefers, and how that changes from session to session.

Reads decisions.jsonl (every question the shop brain answered: options, pick, stats) and
items_report.json (what every item does, from scan_items.py). Only reads files, so it can run while
training is going on.

Usage:
    python shop_report.py          all sessions (the last 6 as columns)
    python shop_report.py last     only the latest session

Sections:
    1. Results: runs, average death wave, how many reached waves 11 / 15 / 20
    2. Level up preference: for every stat, how often it was picked when it was offered
       (4 cards per level up, so 25% = no preference, picking at random)
    3. Shop preference: for every stat, how often an item that raises it was bought when offered
       (and the same for items that lower it)
    4. Habits: gold left when leaving, rerolls and locks per shop, crates taken
    5. Most bought items in the latest session
    6. Every run of the latest session: level ups, items bought, crates taken, build at the last shop

A "session" is one train.py run: runs less than 30 minutes apart belong to the same session.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DECISIONS_FILE = "decisions.jsonl"
ITEMS_FILE = "items_report.json"
SESSION_GAP = timedelta(minutes=30)
MAX_COLUMNS = 6
MIN_OFFERED = 5          # rates from fewer offers are shown in brackets (too few to trust)
LATER_LISTS = {"stats_end_of_wave": "every wave", "stats_next_wave": "next wave",
               "stats_on_level_up": "per level up"}


# ---------- reading ----------

def load_runs(path=DECISIONS_FILE):
    runs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                run = json.loads(line)
                run["_time"] = datetime.strptime(run["time"], "%Y-%m-%d %H:%M:%S")
                runs.append(run)
    runs.sort(key=lambda r: r["_time"])
    return runs


def split_sessions(runs):
    """Runs less than SESSION_GAP apart belong to the same train.py session."""
    sessions = []
    for run in runs:
        if sessions and run["_time"] - sessions[-1][-1]["_time"] <= SESSION_GAP:
            sessions[-1].append(run)
        else:
            sessions.append([run])
    return sessions


def load_item_stats(path=ITEMS_FILE):
    """{item id: {stat label: value}} from scan_items.py. Stats that arrive later get a suffix,
    e.g. "max_hp (every wave)". Scaling effects count as "<stat> (scaling)"."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    out = {}
    for item in items:
        stats = defaultdict(float)
        for effect in item.get("effects", []):
            key = effect.get("key") or ""
            if not key:
                continue
            try:
                value = float(effect.get("value", 0))
            except ValueError:
                continue
            name = key.replace("stat_", "")
            custom = effect.get("custom_key") or ""
            if custom in LATER_LISTS:
                name += f" ({LATER_LISTS[custom]})"
            elif custom:
                name += " (scaling)"
            stats[name] += value
        out[item["id"]] = dict(stats)
    return out


def upgrade_stat(upgrade_id):
    """upgrade_percent_damage_2 -> percent_damage"""
    name = upgrade_id[len("upgrade_"):] if upgrade_id.startswith("upgrade_") else upgrade_id
    head, _, tail = name.rpartition("_")
    return head if tail.isdigit() else name


# ---------- counting ----------

def results(runs):
    waves = [r["wave"] for r in runs]
    n = len(waves)
    won = sum(1 for r in runs if r["end"] == "won")
    return {
        "runs": n,
        "avg wave": sum(waves) / n if n else 0,
        "reached 11": sum(1 for w in waves if w > 11 or w == 11) / n if n else 0,
        "reached 15": sum(1 for w in waves if w >= 15) / n if n else 0,
        "won": won,
    }


def level_up_rates(runs):
    """{stat: [offered, picked]}"""
    counts = defaultdict(lambda: [0, 0])
    for run in runs:
        for d in run["decisions"]:
            if d["kind"] != "level_up":
                continue
            options = [o for o in d["options"] if o]
            for i, option in enumerate(d["options"]):
                if not option:
                    continue
                stat = upgrade_stat(option)
                counts[stat][0] += 1
                if i == d["choice"]:
                    counts[stat][1] += 1
    return counts


def shop_rates(runs, item_stats):
    """{"+stat" or "-stat": [offered, bought]}. An item counts as offered once per shop visit
    (one wave of one run), however many actions it stayed in the shop for."""
    counts = defaultdict(lambda: [0, 0])
    for run in runs:
        offered = defaultdict(set)   # wave -> item ids seen in the shop
        bought = defaultdict(set)    # wave -> item ids bought
        for d in run["decisions"]:
            if d["kind"] != "shop":
                continue
            for option in d["options"]:
                if option.startswith("item_"):
                    offered[d["wave"]].add(option)
            if d["choice"] < 4 and d["choice"] < len(d["options"]):
                option = d["options"][d["choice"]]
                if option.startswith("item_"):
                    bought[d["wave"]].add(option)
        for wave, items in offered.items():
            for item in items:
                for stat, value in item_stats.get(item, {}).items():
                    if value == 0:
                        continue
                    label = ("+" if value > 0 else "-") + stat
                    counts[label][0] += 1
                    if item in bought[wave]:
                        counts[label][1] += 1
    return counts


def habits(runs):
    shop_visits = set()
    rerolls = locks = buys = 0
    leave_gold = []
    crates = Counter()
    for run_index, run in enumerate(runs):
        for d in run["decisions"]:
            if d["kind"] == "shop":
                shop_visits.add((run_index, d["wave"]))
                did = d["did"]
                rerolls += did == "reroll"
                locks += did.startswith("lock")
                buys += did.startswith("buy")
                if did == "leave":
                    leave_gold.append(d["gold"] or 0)
            elif d["kind"] == "crate":
                crates[d["did"]] += 1
    visits = max(1, len(shop_visits))
    crate_total = crates["take"] + crates["recycle"]
    return {
        "gold left when leaving": sum(leave_gold) / len(leave_gold) if leave_gold else 0,
        "buys per shop": buys / visits,
        "rerolls per shop": rerolls / visits,
        "locks per shop": locks / visits,
        "crates taken": crates["take"] / crate_total if crate_total else 0,
    }


def most_bought(runs, top=10):
    counter = Counter()
    for run in runs:
        for d in run["decisions"]:
            if d["kind"] == "shop" and d["did"].startswith("buy") and d["choice"] < len(d["options"]):
                counter[d["options"][d["choice"]]] += 1
    return counter.most_common(top)


# ---------- printing ----------

def rate_text(offered, picked):
    if offered == 0:
        return "-"
    text = f"{100 * picked / offered:.0f}%"
    return text if offered >= MIN_OFFERED else f"({text})"


def frequent(rows_counts):
    """Keep stats offered at least MIN_OFFERED times in some session (the rest is noise)."""
    kept = [(name, counts) for name, counts in rows_counts if any(c[0] >= MIN_OFFERED for c in counts)]
    return kept, len(rows_counts) - len(kept)


def print_table(title, rows, columns, note=""):
    print(f"\n{title}")
    if note:
        print(note)
    name_width = max([len(r[0]) for r in rows] + [10])
    print(" " * name_width + "".join(f"{c:>14}" for c in columns))
    for name, cells in rows:
        print(f"{name:<{name_width}}" + "".join(f"{c:>14}" for c in cells))


def session_label(session):
    start = session[0]["_time"]
    return start.strftime("%m-%d %H:%M")


def main(only_last=None):
    if only_last is None:
        only_last = "last" in sys.argv[1:]
    if not os.path.exists(DECISIONS_FILE):
        print(f"No {DECISIONS_FILE} yet: run train.py first.")
        return
    sessions = split_sessions(load_runs())
    if only_last:
        sessions = sessions[-1:]
    sessions = sessions[-MAX_COLUMNS:]
    item_stats = load_item_stats()
    if not item_stats:
        print(f"(No {ITEMS_FILE}: run python scan_items.py for the shop preference section.)")
    columns = [session_label(s) for s in sessions]
    print(f"Shop brain report: {len(sessions)} session(s). Columns = session start time.")

    # 1. results
    res = [results(s) for s in sessions]
    rows = [("runs", [str(r["runs"]) for r in res]),
            ("avg death wave", [f"{r['avg wave']:.1f}" for r in res]),
            ("reached wave 11", [f"{100 * r['reached 11']:.0f}%" for r in res]),
            ("reached wave 15", [f"{100 * r['reached 15']:.0f}%" for r in res]),
            ("won", [str(r["won"]) for r in res])]
    print_table("1. RESULTS", rows, columns)

    # 2. level up preference
    per_session = [level_up_rates(s) for s in sessions]
    stats = set().union(*[set(c) for c in per_session])
    last = per_session[-1]
    order = sorted(stats, key=lambda s: -(last[s][1] / last[s][0]) if last.get(s, [0])[0] else 1)
    kept, hidden = frequent([(stat, [c.get(stat, [0, 0]) for c in per_session]) for stat in order])
    rows = [(stat, [rate_text(*c) for c in counts]) for stat, counts in kept]
    print_table("2. LEVEL UP: how often each stat was picked when offered", rows, columns,
                "   25% = no preference (picking at random). Sorted by the latest session. "
                f"(brackets) = offered fewer than {MIN_OFFERED} times. {hidden} rarely offered stats hidden.")

    # 3. shop preference
    if item_stats:
        per_session = [shop_rates(s, item_stats) for s in sessions]
        labels = set().union(*[set(c) for c in per_session])
        last = per_session[-1]
        order = sorted(labels, key=lambda s: -(last[s][1] / last[s][0]) if last.get(s, [0])[0] else 1)
        kept, hidden = frequent([(label, [c.get(label, [0, 0]) for c in per_session]) for label in order])
        rows = [(label, [rate_text(*c) for c in counts]) for label, counts in kept]
        print_table("3. SHOP: how often an item with this stat was bought when it was in the shop", rows, columns,
                    "   +stat = the item raises it, -stat = the item lowers it. An item counts once per shop visit. "
                    f"{hidden} rarely offered stats hidden.")

    # 4. habits
    hab = [habits(s) for s in sessions]
    rows = [(name, [f"{h[name]:.0%}" if name == "crates taken" else f"{h[name]:.1f}" for h in hab])
            for name in hab[0]]
    print_table("4. HABITS", rows, columns)

    # 5. most bought
    print(f"\n5. MOST BOUGHT in the session starting {columns[-1]}")
    for item, n in most_bought(sessions[-1]):
        stats_text = ", ".join(f"{k} {v:+g}" for k, v in item_stats.get(item, {}).items())
        print(f"   {n:>3}x {item:<28} {stats_text}")

    # 6. every run
    print_runs(sessions[-1])


def print_runs(runs):
    """Every run of the session: death wave, level ups by stat, items bought, crate items taken,
    and the build's main stats at the last shop."""
    keys = ["max_hp", "percent_damage", "ranged_damage", "attack_speed", "armor", "dodge", "speed",
            "hp_regeneration", "lifesteal", "crit_chance", "range", "luck", "harvesting"]
    print(f"\n6. EVERY RUN in the session starting {runs[0]['_time'].strftime('%m-%d %H:%M')}")
    for i, run in enumerate(runs, 1):
        levels, bought, crates, last = Counter(), [], [], None
        for d in run["decisions"]:
            option = d["options"][d["choice"]] if d["choice"] < len(d["options"]) else ""
            if d["kind"] == "level_up":
                levels[upgrade_stat(option)] += 1
            elif d["kind"] == "shop" and d["did"].startswith("buy"):
                bought.append(option.replace("item_", ""))
            elif d["kind"] == "crate" and d["did"] == "take":
                crates.append(option.replace("item_", ""))
            if d.get("stats"):
                last = d
        print(f"   run {i}: {run['end']} at wave {run['wave']}")
        print("      level ups: " + (", ".join(f"{k} x{v}" for k, v in levels.most_common()) or "none"))
        print("      bought: " + (", ".join(f"{k} x{v}" for k, v in Counter(bought).most_common()) or "nothing"))
        print("      crates taken: " + (", ".join(crates) or "none"))
        if last:
            stats = last["stats"]
            print(f"      build at the wave {last['wave']} shop: " + ", ".join(f"{k} {stats.get(k, 0):g}" for k in keys))


if __name__ == "__main__":
    main()
