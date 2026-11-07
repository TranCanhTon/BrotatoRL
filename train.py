"""Phase 3: the learning AI. PPO from Stable Baselines3 plays Brotato through BrotatoEnv.

It plays N_STEPS turns, pauses the game to learn from them, and repeats until the time is up.
What it learned is saved in models/latest.zip, and the next run of this script continues from it.
Graphs go to tb_logs/ (open them with: tensorboard --logdir tb_logs).

Usage:  python train.py [minutes] [speed]
        python train.py 10 12
"""

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
CHECKPOINT_EVERY = 20_000   # turns between backup copies of the brain (about 6 minutes at your speed)

# ---------- PPO settings (these are the library's defaults, written out so you can see them) ----------
N_STEPS = 2048          # turns played before each learning pause
BATCH_SIZE = 64         # turns looked at together while learning
N_EPOCHS = 10           # times it goes over the same 2048 turns per pause
LEARNING_RATE = 3e-4    # how big each nudge to the brain is
GAMMA = 0.99            # how much later rewards count (0.99 per 0.1 s: the next ~10 s matter most)
NETWORK = [64, 64]      # 2 layers of 64, for both the player and the judge


class RunLogger(BaseCallback):
    """Adds our own graphs to TensorBoard: the wave each run reached, and how often it won."""

    def _on_step(self):
        for done, info in zip(self.locals["dones"], self.locals["infos"]):
            if done and info.get("end_reason") in ("died", "won"):
                self.logger.record_mean("run/wave_reached", info["end_wave"])
                self.logger.record_mean("run/won", 1.0 if info["end_reason"] == "won" else 0.0)
                print(f"[train] run over: {info['end_reason']} at wave {info['end_wave']}")
        return True


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
        print(f"[train] saved to {LATEST} ({model.num_timesteps} turns learned in total)")
        env.close()


if __name__ == "__main__":
    main()
