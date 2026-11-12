"""Head start for the shop brain: train it to copy the teacher (Ton's stat priority, see TeacherShop in
shop_env.py) before it learns on its own.

Reads teacher_data.jsonl (written by `python train.py <minutes> <speed> teach`): for every pick the numbers
the brain sees, what was allowed, what the teacher picked and the waves cleared after it.
Trains both parts of the shop brain on it:
    chooser: make the teacher's pick the most likely one (like studying worked examples)
    judge:   predict how many waves (reward) followed each pick, so it starts out with sensible guesses
Then saves models/shop_v1/latest.zip. The brain it replaces is kept as latest_before_teacher.zip.

Usage:  python pretrain_shop.py            (train.py ... teach runs this at the end by itself)
"""

import json
import os
import random
import shutil
import time

import numpy as np
import torch
import torch.nn.functional as F
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv

from shop_env import (ShopBridge, ShopEnv, TEACHER_FILE, SHOP_OBS_SIZE, SHOP_OBS_VERSION, N_CHOICES,
                      CHOICE_LEAVE, CHOICE_REROLL, N_SLOTS)

SHOP_DIR = os.path.join("models", f"shop_v{SHOP_OBS_VERSION}")
SHOP_LATEST = os.path.join(SHOP_DIR, "latest.zip")
SHOP_BEFORE = os.path.join(SHOP_DIR, "latest_before_teacher.zip")
GAMMA = 0.99            # same as train.py SHOP_GAMMA
EPOCHS = 40             # passes over all teacher picks
BATCH_SIZE = 128
LEARNING_RATE = 1e-3
VALUE_WEIGHT = 0.5
NETWORK = [128, 128]    # same as train.py SHOP_NETWORK, used only if there is no shop brain yet


def load_teacher_data(path=TEACHER_FILE):
    """All recorded picks as arrays, with the discounted reward that followed each pick (the judge's target)."""
    obs, masks, actions, returns, kinds = [], [], [], [], []
    runs = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            run = json.loads(line)
            samples = run["samples"]
            runs += 1
            future = 0.0
            run_returns = []
            for sample in reversed(samples):
                future = sample["reward"] + GAMMA * future
                run_returns.append(future)
            run_returns.reverse()
            for sample, ret in zip(samples, run_returns):
                obs.append(sample["obs"])
                masks.append(sample["mask"])
                actions.append(sample["action"])
                returns.append(ret)
                kinds.append(int(np.argmax(sample["obs"][:3])))   # 0 shop, 1 level up, 2 crate
    return (np.asarray(obs, dtype=np.float32), np.asarray(masks, dtype=bool), np.asarray(actions, dtype=np.int64),
            np.asarray(returns, dtype=np.float32), np.asarray(kinds), runs)


def load_or_new_model():
    if os.path.exists(SHOP_LATEST):
        shutil.copyfile(SHOP_LATEST, SHOP_BEFORE)
        print(f"[pretrain] starting from {SHOP_LATEST} (copy kept as {SHOP_BEFORE})")
        return MaskablePPO.load(SHOP_LATEST, device="cpu")
    print("[pretrain] no shop brain yet, making a fresh one")
    env = DummyVecEnv([lambda: ShopEnv(ShopBridge(verbose=False))])
    return MaskablePPO("MlpPolicy", env, policy_kwargs={"net_arch": NETWORK}, device="cpu", verbose=0)


def agreement(policy, obs, masks, actions, kinds):
    """How often the brain's most likely pick is the teacher's pick: overall, and per kind of question."""
    with torch.no_grad():
        dist = policy.get_distribution(torch.as_tensor(obs), action_masks=masks)
        picks = dist.get_actions(deterministic=True).cpu().numpy()
    same = picks == actions
    per_kind = {name: float(same[kinds == k].mean()) if (kinds == k).any() else None
                for k, name in enumerate(["shop", "level up", "crate"])}
    return float(same.mean()), per_kind


def main():
    if not os.path.exists(TEACHER_FILE):
        print(f"[pretrain] no {TEACHER_FILE} yet: run python train.py 120 4 teach first.")
        return
    obs, masks, actions, returns, kinds, runs = load_teacher_data()
    if len(obs) == 0:
        print(f"[pretrain] {TEACHER_FILE} has no picks yet.")
        return
    if obs.shape[1] != SHOP_OBS_SIZE:
        print(f"[pretrain] {TEACHER_FILE} has {obs.shape[1]} numbers per pick, expected {SHOP_OBS_SIZE}. Skipping.")
        return
    counts = np.bincount(kinds, minlength=3)
    print(f"[pretrain] {len(obs)} teacher picks from {runs} runs "
          f"({counts[0]} shop, {counts[1]} level up, {counts[2]} crate)")

    model = load_or_new_model()
    policy = model.policy
    policy.set_training_mode(True)
    optimizer = torch.optim.Adam(policy.parameters(), lr=LEARNING_RATE)

    before, _ = agreement(policy, obs, masks, actions, kinds)
    print(f"[pretrain] before: the brain agrees with the teacher on {before:.0%} of picks")
    start = time.time()
    order = list(range(len(obs)))
    for epoch in range(1, EPOCHS + 1):
        random.shuffle(order)
        total_choice, total_value, batches = 0.0, 0.0, 0
        for i in range(0, len(order), BATCH_SIZE):
            idx = order[i:i + BATCH_SIZE]
            values, log_prob, _ = policy.evaluate_actions(
                torch.as_tensor(obs[idx]), torch.as_tensor(actions[idx]), action_masks=masks[idx])
            choice_loss = -log_prob.mean()
            value_loss = F.mse_loss(values.flatten(), torch.as_tensor(returns[idx]))
            loss = choice_loss + VALUE_WEIGHT * value_loss
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), 0.5)
            optimizer.step()
            total_choice += float(choice_loss)
            total_value += float(value_loss)
            batches += 1
        if epoch % 10 == 0 or epoch == 1:
            agree, _ = agreement(policy, obs, masks, actions, kinds)
            print(f"[pretrain] pass {epoch}/{EPOCHS}: agrees {agree:.0%}, choice loss {total_choice / batches:.3f}, "
                  f"judge error {total_value / batches:.2f}")
    policy.set_training_mode(False)

    agree, per_kind = agreement(policy, obs, masks, actions, kinds)
    kinds_text = ", ".join(f"{name} {v:.0%}" for name, v in per_kind.items() if v is not None)
    print(f"[pretrain] done in {time.time() - start:.0f} s: agrees with the teacher on {agree:.0%} of picks ({kinds_text})")
    os.makedirs(SHOP_DIR, exist_ok=True)
    model.save(SHOP_LATEST)
    print(f"[pretrain] saved to {SHOP_LATEST}. Next: python train.py 480 4 (the brain learns on its own from here)")


if __name__ == "__main__":
    main()
