"""Watch the trained AI play. Loads models/v2/latest.zip and plays runs. No learning, nothing is saved.

By default it always takes the move it rates best, which shows what it has really learned.
Level ups, crates and the shop are clicked slowly, and the button about to be pressed lights up first.
The shop brain (models/shop_v1/latest.zip) makes those picks and prints each one. Without a saved
shop brain the picks are random.
Add "dice" to let it roll the dice with its odds instead, the way it plays during training.
Add "best" to play models/v2/best.zip (best average so far) instead of latest.zip.

Usage:  python watch.py [speed] [dice] [best]
        python watch.py              normal speed, best move, latest brain
        python watch.py 2            2x speed
        python watch.py 1 dice       normal speed, rolls the dice like in training
        python watch.py 4 best       4x speed, the best brain so far
Stop with Ctrl+C.
"""

import os
import sys
import time

from stable_baselines3 import PPO

from sb3_contrib import MaskablePPO

from brotato_env import BrotatoEnv, OBS_VERSION
from run_random import print_report, print_summary
from shop_env import FrozenShop, RandomShop, SHOP_OBS_VERSION

ARGS = sys.argv[1:]
NUMBERS = [a for a in ARGS if a.replace(".", "", 1).isdigit()]
SPEED = float(NUMBERS[0]) if NUMBERS else 1.0
DICE = "dice" in ARGS
MODEL = os.path.join("models", f"v{OBS_VERSION}", "best.zip" if "best" in ARGS else "latest.zip")
SHOP_MODEL = os.path.join("models", f"shop_v{SHOP_OBS_VERSION}", "latest.zip")


def play_one_run(env, model):
    obs, info = env.reset()
    run_start = time.time()
    total_reward, turns = 0.0, 0
    while True:
        move, _ = model.predict(obs, deterministic=not DICE)
        obs, reward, terminated, truncated, info = env.step(int(move))
        total_reward += reward
        turns += 1
        if terminated or truncated:
            return (info.get("end_reason"), info.get("end_wave"), total_reward, turns,
                    time.time() - run_start, info.get("report"))


def main():
    if not os.path.exists(MODEL):
        print(f"No trained brain found at {MODEL}. Run train.py first.")
        return
    model = PPO.load(MODEL, device="cpu")
    if os.path.exists(SHOP_MODEL):
        shop_model = MaskablePPO.load(SHOP_MODEL, device="cpu")
        shop = FrozenShop(shop_model, deterministic=not DICE)
        print(f"Shop brain: {SHOP_MODEL} ({shop_model.num_timesteps} decisions of training).")
    else:
        shop = RandomShop()
        print(f"No shop brain at {SHOP_MODEL} yet: level ups, crates and the shop are picked at random.")
    env = BrotatoEnv(speed=SPEED, verbose=False, watch=True, shop=shop)
    style = "rolling the dice like in training" if DICE else "always taking its best move"
    print(f"Watching {MODEL} ({model.num_timesteps} turns of training) at {SPEED:g}x, {style}.")
    print("Start Brotato now. Stop with Ctrl+C.")

    runs = []
    start = time.time()
    try:
        while True:
            result = play_one_run(env, model)
            runs.append(result)
            why, wave, reward, turns, seconds, report = result
            print(f"run {len(runs):3d}: {why:17s} wave {wave:2d} | reward {reward:7.1f} | "
                  f"{turns:5d} turns | {seconds:5.0f} s")
            print_report(report)
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        env.close()

    if runs:
        print_summary(runs, (time.time() - start) / 60)


if __name__ == "__main__":
    main()
