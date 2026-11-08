"""Phase 3: the learning AI. PPO from Stable Baselines3 plays Brotato through BrotatoEnv.

It plays N_STEPS turns, pauses the game to learn from them, and repeats until the time is up.
What it learned is saved in models/latest.zip, and the next run of this script continues from it.
Graphs go to tb_logs/ (open them with: tensorboard --logdir tb_logs).
Everything printed also goes to train_log.txt (added to the end, never overwritten).
One row per finished run goes to runs.csv (open it in Excel): wave, reward, materials, damage and so on.

Usage:  python train.py [minutes] [speed]
        python train.py 10 12
"""

import csv
import os
import sys
import time

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback

from brotato_env import BrotatoEnv

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0

MODEL_DIR = "models"
LATEST = os.path.join(MODEL_DIR, "latest.zip")
LOG_DIR = "tb_logs"
LOG_FILE = "train_log.txt"
RUNS_FILE = "runs.csv"
RUNS_COLUMNS = ["time", "total_turns", "end", "wave", "reward", "turns", "materials", "materials_per_wave",
                "damage_taken", "wall_time", "reward_alive", "reward_hit", "reward_material", "reward_wall",
                "reward_wave", "reward_won", "reward_died"]
CHECKPOINT_EVERY = 20_000   # turns between backup copies of the brain (about 6 minutes at your speed)

# ---------- PPO settings (these are the library's defaults, written out so you can see them) ----------
N_STEPS = 2048          # turns played before each learning pause
BATCH_SIZE = 64         # turns looked at together while learning
N_EPOCHS = 10           # times it goes over the same 2048 turns per pause
LEARNING_RATE = 3e-4    # how big each nudge to the brain is
GAMMA = 0.99            # how much later rewards count (0.99 per 0.1 s: the next ~10 s matter most)
NETWORK = [64, 64]      # 2 layers of 64, for both the player and the judge


class Tee:
    """Sends everything printed to both the terminal and train_log.txt."""

    def __init__(self, terminal, path):
        self.terminal = terminal
        self.file = open(path, "a", encoding="utf-8")

    def write(self, text):
        self.terminal.write(text)
        self.file.write(text)
        self.file.flush()   # write it to disk right away, so the file is always up to date

    def flush(self):
        self.terminal.flush()
        self.file.flush()


class RunLogger(BaseCallback):
    """After every finished run: our own TensorBoard graphs, one row in runs.csv, one line in the terminal.

    Graphs (run/...): wave reached, won, materials picked up (total and per wave), damage taken,
    share of time near a wall. And reward_parts/...: the run's reward split into its parts.
    """

    def _on_step(self):
        for done, info in zip(self.locals["dones"], self.locals["infos"]):
            if done and info.get("end_reason") in ("died", "won"):
                self._log_run(info)
        return True

    def _log_run(self, info):
        st = info.get("stats") or {}
        turns = max(1, st.get("turns", 0))
        waves_played = st.get("waves_cleared", 0) + (0 if info["end_reason"] == "won" else 1)
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_turns": self.num_timesteps,
            "end": info["end_reason"],
            "wave": info["end_wave"],
            "reward": round(sum(v for k, v in st.items() if k.startswith("reward_")), 2),
            "turns": st.get("turns", 0),
            "materials": st.get("materials", 0),
            "materials_per_wave": round(st.get("materials", 0) / max(1, waves_played), 1),
            "damage_taken": round(st.get("damage_taken", 0.0), 2),
            "wall_time": round(st.get("wall_turns", 0) / turns, 3),
        }
        for key in RUNS_COLUMNS:
            if key.startswith("reward_"):
                row[key] = round(st.get(key, 0.0), 2)

        self.logger.record_mean("run/wave_reached", info["end_wave"])
        self.logger.record_mean("run/won", 1.0 if info["end_reason"] == "won" else 0.0)
        for key in ("materials", "materials_per_wave", "damage_taken", "wall_time"):
            self.logger.record_mean(f"run/{key}", row[key])
        for key in RUNS_COLUMNS:
            if key.startswith("reward_"):
                self.logger.record_mean(f"reward_parts/{key[len('reward_'):]}", row[key])

        new_file = not os.path.exists(RUNS_FILE)
        with open(RUNS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RUNS_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)

        print(f"[train] {row['time'][11:]} run over: {info['end_reason']} at wave {info['end_wave']} | "
              f"reward {row['reward']} | materials {row['materials']} | damage {row['damage_taken']} max HP | "
              f"near wall {row['wall_time'] * 100:.0f}% of the time")


class StopAfter(BaseCallback):
    """Stops training once the time is up (checked every turn)."""

    def __init__(self, minutes):
        super().__init__()
        self.seconds = minutes * 60
        self.start = None

    def _on_step(self):
        if self.start is None:
            self.start = time.time()   # clock starts at the first turn, not while waiting for the game
        return time.time() - self.start < self.seconds


def make_or_load_model(env):
    """Continue from models/latest.zip if it exists, otherwise start a fresh brain."""
    if os.path.exists(LATEST):
        print(f"[train] continuing from {LATEST}")
        return PPO.load(LATEST, env=env, device="cpu", tensorboard_log=LOG_DIR)
    print("[train] no saved brain yet, starting fresh (it will play randomly at first)")
    return PPO(
        "MlpPolicy", env,
        n_steps=N_STEPS, batch_size=BATCH_SIZE, n_epochs=N_EPOCHS,
        learning_rate=LEARNING_RATE, gamma=GAMMA,
        policy_kwargs={"net_arch": NETWORK},
        tensorboard_log=LOG_DIR, device="cpu", verbose=1,
    )


def main():
    sys.stdout = Tee(sys.stdout, LOG_FILE)
    print(f"\n========== training started {time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    os.makedirs(MODEL_DIR, exist_ok=True)
    env = BrotatoEnv(speed=SPEED, verbose=False)
    model = make_or_load_model(env)
    print(f"[train] training for {MINUTES:g} minutes at {SPEED:g}x. Start Brotato now, then don't touch anything.")
    print("[train] the game freezes for a few seconds every 2048 turns while the AI learns, that's normal.")

    callbacks = [
        RunLogger(),
        StopAfter(MINUTES),
        CheckpointCallback(save_freq=CHECKPOINT_EVERY, save_path=MODEL_DIR, name_prefix="checkpoint"),
    ]
    try:
        model.learn(total_timesteps=10**9, callback=callbacks, tb_log_name="ppo",
                    reset_num_timesteps=False)
    except KeyboardInterrupt:
        print("[train] stopped with Ctrl+C")
    finally:
        model.save(LATEST)
        print(f"[train] {time.strftime('%H:%M:%S')} saved to {LATEST} ({model.num_timesteps} turns learned in total)")
        env.close()


if __name__ == "__main__":
    main()
