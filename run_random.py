"""Phase 2 final test: random moves play runs back to back through BrotatoEnv.

Usage:  python run_random.py [minutes] [speed]
        python run_random.py 30 20

Prints one line per run and a summary at the end (or when you press Ctrl+C).
"""

import sys
import time

from brotato_env import BrotatoEnv

HOLD_TURNS = 5   # keep each random move for 0.5 s of game time, like listen.py


def play_one_run(env):
    obs, info = env.reset()
    run_start = time.time()
    total_reward, turns, move = 0.0, 0, 0
    while True:
        if turns % HOLD_TURNS == 0:
            move = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(move)
        total_reward += reward
        turns += 1
        if terminated or truncated:
            return (info.get("end_reason"), info.get("end_wave"), total_reward, turns,
                    time.time() - run_start, info.get("report"))


def stats_text(stats):
    """{"max_hp": 3, "speed": -2} -> "max_hp +3, speed -2" """
    return ", ".join(f"{name} {value:+g}" for name, value in stats.items() if value) or "no stats"


def print_report(report):
    """What the autopilot bought and picked this run, then the final character stats."""
    if not report:
        return
    print("  ----- run report -----")
    for entry in report["log"]:
        price = f" for {entry['price']}" if entry["price"] else ""
        print(f"  wave {entry['wave']:2d}  {entry['action']:14s} {entry['id']}{price}  ({stats_text(entry['stats'])})")
    print("  final stats: " + ", ".join(f"{name} {value:g}" for name, value in report["stats"].items()))


def print_summary(runs, minutes):
    print()
    print(f"Summary: {len(runs)} runs in {minutes:.1f} minutes")
    print(f"  won (cleared the last wave): {sum(1 for r in runs if r[0] == 'won')}")
    print(f"  died: {sum(1 for r in runs if r[0] == 'died')}")
    print(f"  ended some other way (should be 0): {sum(1 for r in runs if r[0] not in ('won', 'died'))}")
    print(f"  average wave reached: {sum(r[1] for r in runs) / len(runs):.1f}")
    print(f"  average reward: {sum(r[2] for r in runs) / len(runs):.1f}")
    print(f"  turns per real second: {sum(r[3] for r in runs) / max(1.0, minutes * 60):.0f}")


def main():
    # read here, not at the top, so rule_bot.py and watch.py can import this file with their own arguments
    MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    env = BrotatoEnv(speed=SPEED, verbose=False)
    print(f"Random runs for {MINUTES:.0f} minutes at {SPEED:.0f}x. Start Brotato now, then don't touch anything.")

    runs = []
    start = None
    try:
        while start is None or time.time() - start < MINUTES * 60:
            result = play_one_run(env)
            if start is None:
                start = time.time() - result[4]   # clock starts at the first run's first turn
            runs.append(result)
            why, wave, reward, turns, seconds, report = result
            print(f"run {len(runs):3d}: {why:17s} wave {wave:2d} | reward {reward:7.1f} | "
                  f"{turns:5d} turns | {seconds:5.0f} s | total {(time.time() - start) / 60:5.1f} min")
            print_report(report)
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        env.close()

    if runs:
        print_summary(runs, (time.time() - start) / 60)


if __name__ == "__main__":
    main()
