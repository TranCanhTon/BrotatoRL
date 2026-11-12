"""The learning AI. PPO from Stable Baselines3 plays Brotato through BrotatoEnv.

It plays N_STEPS turns, pauses the game to learn from them, and repeats until the time is up.
Brain version 2 (161 numbers in, see brotato_env.py) lives in models/v2/:
  latest.zip   the brain at the end of the last session, the next session continues from it
  best.zip     the brain with the best average reward over BEST_WINDOW runs so far (best.json says when)
  checkpoint_* backups every CHECKPOINT_EVERY turns
The old brain (73 numbers) stays untouched in models/latest.zip.
Graphs go to tb_logs/ as ppo_v2_... (open them with: tensorboard --logdir tb_logs).
Everything printed also goes to train_log.txt (added to the end, never overwritten).
One row per finished run goes to runs_v2.csv (open it in Excel): wave, reward, materials, damage and so on.
The last 5 s before every death go to deaths_v2.jsonl (one line per death).

The shop brain (level ups, crates, shop) learns at the same time, in its own thread (see shop_env.py).
It lives in models/shop_v1/ (latest.zip, checkpoints), graphs in tb_logs/ as shop_v1_...,
and every run's questions and picks go to decisions.jsonl.

Usage:  python train.py [minutes] [speed]
        python train.py 10 4
"""

import collections
import csv
import json
import os
import sys
import time

import threading
import traceback

import torch
from sb3_contrib import MaskablePPO
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor

from brotato_env import BrotatoEnv, OBS_SIZE, OBS_VERSION
from shop_env import ShopBridge, ShopEnv, TeacherShop, SHOP_OBS_SIZE, SHOP_OBS_VERSION

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
# "teach": Ton's old rules make the shop picks (the shop brain does not learn), every pick is recorded in
# teacher_data.jsonl, and at the end the shop brain is trained to copy them (pretrain_shop.py)
TEACH = "teach" in sys.argv[3:]

MODEL_DIR = os.path.join("models", f"v{OBS_VERSION}")
LATEST = os.path.join(MODEL_DIR, "latest.zip")
BEST = os.path.join(MODEL_DIR, "best.zip")
BEST_INFO = os.path.join(MODEL_DIR, "best.json")
BRAIN_INFO = os.path.join(MODEL_DIR, "brain_info.json")
LOG_DIR = "tb_logs"
LOG_FILE = "train_log.txt"
RUNS_FILE = f"runs_v{OBS_VERSION}.csv"
DEATHS_FILE = f"deaths_v{OBS_VERSION}.jsonl"
RUNS_COLUMNS = ["time", "total_turns", "end", "wave", "reward", "turns", "materials", "materials_per_wave",
                "damage_taken", "wall_time", "reward_alive", "reward_hit", "reward_material", "reward_wall",
                "reward_wave", "reward_boss", "reward_won", "reward_died"]
CHECKPOINT_EVERY = 20_000   # turns between backup copies of the brain (about 13 minutes at speed 4)
BEST_WINDOW = 20            # best.zip = best average reward over this many runs in a row

# ---------- PPO settings ----------
N_STEPS = 2048          # turns played before each learning pause
BATCH_SIZE = 64         # turns looked at together while learning
N_EPOCHS = 10           # times it goes over the same 2048 turns per pause
LEARNING_RATE = 3e-4    # how big each nudge to the brain is
GAMMA = 0.995           # how much later rewards count (0.995 per 0.1 s: the next ~20 s matter most, was 0.99 = ~10 s)
ENT_COEF = 0.01         # small bonus for trying different moves, so it explores before settling (was 0)
NETWORK = [256, 256]    # 2 layers of 256, for both the player and the judge (was 64: more inputs need more room)
CPU_THREADS = 2         # cores Python may use while learning, so the rest of the computer stays smooth

# ---------- shop brain ----------
SHOP_DIR = os.path.join("models", f"shop_v{SHOP_OBS_VERSION}")
SHOP_LATEST = os.path.join(SHOP_DIR, "latest.zip")
SHOP_N_STEPS = 256          # decisions before each learning pause (about 2 to 3 runs)
SHOP_BATCH_SIZE = 64
SHOP_N_EPOCHS = 10
SHOP_LEARNING_RATE = 3e-4
SHOP_GAMMA = 0.99           # per decision: about the next 100 decisions (~15 waves) matter
SHOP_ENT_COEF = 0.01
SHOP_NETWORK = [128, 128]
SHOP_CHECKPOINT_EVERY = 2_000   # decisions between backups (about 15 to 20 runs)


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
    """After every finished run: TensorBoard graphs, one row in runs_v2.csv, one line in the terminal,
    the last 5 s in deaths_v2.jsonl if it died, and best.zip if the recent average beat the record.

    Graphs (run/...): wave reached, won, materials picked up (total and per wave), damage taken,
    share of time near a wall. And reward_parts/...: the run's reward split into its parts.
    """

    def __init__(self):
        super().__init__()
        self.recent_rewards = collections.deque(maxlen=BEST_WINDOW)
        self.best = None
        if os.path.exists(BEST_INFO):
            with open(BEST_INFO, encoding="utf-8") as f:
                self.best = json.load(f)["average_reward"]

    def _on_step(self):
        for done, info in zip(self.locals["dones"], self.locals["infos"]):
            if done and info.get("end_reason") in ("died", "won"):
                self._log_run(info)
        return True

    def _save_death(self, info, row):
        """The last 5 s before a death, one line per death, so we can see how it dies."""
        if info["end_reason"] != "died":
            return
        entry = {"time": row["time"], "total_turns": self.num_timesteps, "wave": info["end_wave"],
                 "last_turns": info.get("last_turns", [])}
        with open(DEATHS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _check_best(self, reward):
        """Saves best.zip when the average of the last BEST_WINDOW runs beats the record."""
        self.recent_rewards.append(reward)
        if len(self.recent_rewards) < BEST_WINDOW:
            return
        average = sum(self.recent_rewards) / BEST_WINDOW
        if self.best is not None and average <= self.best:
            return
        self.best = average
        self.model.save(BEST)
        with open(BEST_INFO, "w", encoding="utf-8") as f:
            json.dump({"average_reward": round(average, 2), "runs": BEST_WINDOW,
                       "total_turns": self.num_timesteps, "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
        print(f"[train] new best: average reward {average:.1f} over the last {BEST_WINDOW} runs, saved to {BEST}")

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
        self._save_death(info, row)
        self._check_best(row["reward"])


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


def check_brain_info():
    """Stops with a clear message if the saved brain was made for a different layout of numbers."""
    if not os.path.exists(BRAIN_INFO):
        return
    with open(BRAIN_INFO, encoding="utf-8") as f:
        saved = json.load(f)
    if saved.get("obs_version") != OBS_VERSION or saved.get("obs_size") != OBS_SIZE:
        raise SystemExit(f"[train] {LATEST} was trained on layout version {saved.get('obs_version')} "
                         f"({saved.get('obs_size')} numbers), but brotato_env.py is version {OBS_VERSION} "
                         f"({OBS_SIZE} numbers). Use a different models folder or move the old brain away.")


def write_brain_info():
    with open(BRAIN_INFO, "w", encoding="utf-8") as f:
        json.dump({"obs_version": OBS_VERSION, "obs_size": OBS_SIZE, "network": NETWORK, "gamma": GAMMA,
                   "ent_coef": ENT_COEF}, f, indent=2)


def make_or_load_model(env):
    """Continue from models/v2/latest.zip if it exists, otherwise start a fresh brain."""
    if os.path.exists(LATEST):
        check_brain_info()
        print(f"[train] continuing from {LATEST}")
        return PPO.load(LATEST, env=env, device="cpu", tensorboard_log=LOG_DIR)
    print("[train] no saved brain yet, starting fresh (it will play randomly at first)")
    write_brain_info()
    return PPO(
        "MlpPolicy", env,
        n_steps=N_STEPS, batch_size=BATCH_SIZE, n_epochs=N_EPOCHS,
        learning_rate=LEARNING_RATE, gamma=GAMMA, ent_coef=ENT_COEF,
        policy_kwargs={"net_arch": NETWORK},
        tensorboard_log=LOG_DIR, device="cpu", verbose=1,
    )


class StopWhenAsked(BaseCallback):
    """Stops the shop brain's learning once train.py is finishing."""

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge

    def _on_step(self):
        return not self.bridge.stopping.is_set()


def make_or_load_shop_model(bridge):
    """Continue from models/shop_v1/latest.zip if it exists, otherwise a fresh shop brain."""
    env = VecMonitor(DummyVecEnv([lambda: ShopEnv(bridge)]))
    if os.path.exists(SHOP_LATEST):
        print(f"[train] shop brain: continuing from {SHOP_LATEST}")
        return MaskablePPO.load(SHOP_LATEST, env=env, device="cpu", tensorboard_log=LOG_DIR)
    print("[train] shop brain: none saved yet, starting fresh (it will pick at random at first)")
    return MaskablePPO(
        "MlpPolicy", env,
        n_steps=SHOP_N_STEPS, batch_size=SHOP_BATCH_SIZE, n_epochs=SHOP_N_EPOCHS,
        learning_rate=SHOP_LEARNING_RATE, gamma=SHOP_GAMMA, ent_coef=SHOP_ENT_COEF,
        policy_kwargs={"net_arch": SHOP_NETWORK},
        tensorboard_log=LOG_DIR, device="cpu", verbose=0,
    )


def run_shop_brain(bridge, shop_model):
    """The shop brain's own thread: learns from the questions the game asks, until train.py stops."""
    callbacks = [StopWhenAsked(bridge),
                 CheckpointCallback(save_freq=SHOP_CHECKPOINT_EVERY, save_path=SHOP_DIR, name_prefix="checkpoint")]
    try:
        shop_model.learn(total_timesteps=10**9, callback=callbacks, tb_log_name=f"shop_v{SHOP_OBS_VERSION}",
                         reset_num_timesteps=False)
    except Exception:
        print("[train] shop brain crashed:")
        traceback.print_exc()
    finally:
        shop_model.save(SHOP_LATEST)
        print(f"[train] {time.strftime('%H:%M:%S')} shop brain saved to {SHOP_LATEST} "
              f"({shop_model.num_timesteps} decisions learned in total)")


def main():
    sys.stdout = Tee(sys.stdout, LOG_FILE)
    torch.set_num_threads(CPU_THREADS)
    print(f"\n========== training started {time.strftime('%Y-%m-%d %H:%M:%S')} (brain v{OBS_VERSION}) ==========")
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(SHOP_DIR, exist_ok=True)
    bridge = ShopBridge()
    shop_thread = None
    if TEACH:
        print("[train] teach mode: the old rules pick in the shop and every pick is recorded in teacher_data.jsonl. "
              "The shop brain copies them at the end.")
        shop = TeacherShop()
    else:
        shop_model = make_or_load_shop_model(bridge)
        shop_thread = threading.Thread(target=run_shop_brain, args=(bridge, shop_model), daemon=True)
        shop_thread.start()
        shop = bridge
    env = BrotatoEnv(speed=SPEED, verbose=False, shop=shop)
    model = make_or_load_model(env)
    print(f"[train] training for {MINUTES:g} minutes at {SPEED:g}x. Start Brotato now, then don't touch anything.")
    print("[train] the game freezes for a few seconds every 2048 turns while the AI learns, that's normal.")
    if SPEED > 4:
        print(f"[train] WARNING: speed {SPEED:g} is above 4. Your laptop lagged at 12x and the AI played much worse.")

    callbacks = [
        RunLogger(),
        StopAfter(MINUTES),
        CheckpointCallback(save_freq=CHECKPOINT_EVERY, save_path=MODEL_DIR, name_prefix="checkpoint"),
    ]
    try:
        model.learn(total_timesteps=10**9, callback=callbacks, tb_log_name=f"ppo_v{OBS_VERSION}",
                    reset_num_timesteps=False)
    except KeyboardInterrupt:
        print("[train] stopped with Ctrl+C")
    finally:
        model.save(LATEST)
        print(f"[train] {time.strftime('%H:%M:%S')} saved to {LATEST} ({model.num_timesteps} turns learned in total)")
        if shop_thread is not None:
            bridge.stop()
            shop_thread.join(timeout=120)
            if shop_thread.is_alive():
                print("[train] WARNING: the shop brain did not stop in time, it was NOT saved this session")
        try:
            import shop_report
            print("\n[train] what the shop brain picked this session (python shop_report.py for all sessions):")
            shop_report.main(only_last=True)
        except Exception as error:
            print(f"[train] could not print the shop report: {error}")
        env.close()
        if TEACH:
            print("\n[train] now teaching the shop brain to copy the old rules (pretrain_shop.py)...")
            try:
                import pretrain_shop
                pretrain_shop.main()
            except Exception as error:
                print(f"[train] copying failed: {error}. Run python pretrain_shop.py to try again.")


if __name__ == "__main__":
    main()
