"""Watch the trained AI play. Loads models/latest.zip and plays runs. No learning, nothing is saved.

By default it always takes the move it rates best, which shows what it has really learned.
Add "dice" to let it roll the dice with its odds instead, the way it plays during training.

Usage:  python watch.py [speed] [dice]
        python watch.py          normal speed, best move
        python watch.py 2        2x speed
        python watch.py 1 dice   normal speed, rolls the dice like in training
Stop with Ctrl+C.
"""

import os
import sys
import time

from stable_baselines3 import PPO

from brotato_env import BrotatoEnv
from run_random import print_report, print_summary

SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
DICE = len(sys.argv) > 2 and sys.argv[2] == "dice"
MODEL = os.path.join("models", "latest.zip")


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
    env = BrotatoEnv(speed=SPEED, verbose=False)
    style = "rolling the dice like in training" if DICE else "always taking its best move"
    print(f"Watching the AI ({model.num_timesteps} turns of training) at {SPEED:g}x, {style}.")
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
