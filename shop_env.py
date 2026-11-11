"""The shop brain: level ups, crates and the shop, decided by a second AI.

The mod asks a question every time something has to be picked between waves:
    {"decision": "shop" | "level_up" | "crate", "obs": [SHOP_OBS_SIZE numbers], "mask": [N_CHOICES true/false],
     "info": {"shop_obs_version", "wave", "gold", "options": [item ids], "prices": [...]}}
and waits for {"choice": n}. brotato_env.py receives these questions (they arrive in the middle of the
movement brain's turns) and hands them to a "shop" object from this file:

    RandomShop      picks a random allowed choice (run_random.py, rule_bot.py, listen.py)
    FrozenShop      a trained shop brain that does not learn (watch.py)
    ShopBridge      passes questions to ShopEnv, where MaskablePPO learns (train.py)

One episode for the shop brain = one run. Reward: +REWARD_WAVE for every wave cleared after a decision,
+REWARD_BOSS_WAVE extra for boss waves, +REWARD_WON extra for clearing the last wave. Nothing for dying:
the waves just stop coming.
"""

import json
import queue
import random
import threading
import time

import gymnasium as gym
import numpy as np
from gymnasium import spaces

# ---------- layout of the numbers (must match the mod, shop version 1) ----------
SHOP_OBS_VERSION = 1
N_SLOTS = 4
N_CHOICES = 10                   # shop: 0-3 buy slot, 4-7 lock slot, 8 reroll, 9 leave
CHOICE_REROLL, CHOICE_LEAVE = 8, 9
CONTEXT = 17                     # decision kind 3, situation 10, last wave 4
N_STATS = 44                     # own stats now (SHOP_STATS in the mod)
N_LATER = 42                     # own "later" pages: every wave, next wave, every level up (14 stats each)
OPTION_SIZE = 8 + N_STATS + N_LATER  # header 8, what it changes now, what it writes on the later pages
OPTIONS = CONTEXT + N_STATS + N_LATER
SHOP_OBS_SIZE = OPTIONS + N_SLOTS * OPTION_SIZE   # 479
OBS_LIMIT = 3.0                  # the mod clamps every number to -3..3

# ---------- rewards ----------
REWARD_WAVE = 1.0
REWARD_BOSS_WAVE = 2.0           # extra
BOSS_WAVES = (11, 15, 18, 20)
REWARD_WON = 5.0                 # extra

DECISIONS_FILE = "decisions.jsonl"   # one line per run: every question, what was offered, what it picked
ANSWER_TIMEOUT = 15.0            # seconds the game waits for the shop brain before a random pick (mod gives up at 20)


def choice_name(kind, choice):
    if kind == "shop":
        if choice < N_SLOTS:
            return f"buy {choice + 1}"
        if choice < 2 * N_SLOTS:
            return f"lock {choice - N_SLOTS + 1}"
        return "reroll" if choice == CHOICE_REROLL else "leave"
    if kind == "crate":
        return "take" if choice == 0 else "recycle"
    return f"pick {choice + 1}"


def wave_reward(wave, last_wave):
    reward = REWARD_WAVE
    if wave in BOSS_WAVES:
        reward += REWARD_BOSS_WAVE
    if wave >= last_wave:
        reward += REWARD_WON
    return reward


def read_question(msg):
    """Checks the layout and returns (obs, mask) as numpy arrays."""
    version = msg.get("info", {}).get("shop_obs_version")
    if version != SHOP_OBS_VERSION or len(msg["obs"]) != SHOP_OBS_SIZE:
        raise RuntimeError(
            f"The mod sends shop layout version {version} with {len(msg['obs'])} numbers, but shop_env.py "
            f"expects version {SHOP_OBS_VERSION} with {SHOP_OBS_SIZE}. Close Brotato, run python make_test_mod.py, "
            f"start it again.")
    return np.asarray(msg["obs"], dtype=np.float32), np.asarray(msg["mask"], dtype=bool)


def random_choice(mask):
    allowed = [i for i, ok in enumerate(mask) if ok]
    return random.choice(allowed) if allowed else CHOICE_LEAVE


class RandomShop:
    """Picks a random allowed choice. Used when no shop brain is given."""

    def decide(self, msg):
        return random_choice(msg["mask"])

    def on_wave_cleared(self, wave, last_wave):
        pass

    def on_run_end(self, end_reason, wave):
        pass


class RunTally:
    """Counts what the shop brain did this run, for one printed line and decisions.jsonl."""

    def __init__(self):
        self.decisions = []

    def add(self, msg, choice):
        info = msg.get("info", {})
        self.decisions.append({"wave": info.get("wave"), "kind": msg["decision"], "gold": info.get("gold"),
                               "options": info.get("options"), "prices": info.get("prices"),
                               "choice": int(choice), "did": choice_name(msg["decision"], choice),
                               "stats": info.get("stats", {})})

    def summary(self):
        did = [d["did"] for d in self.decisions]
        bought = sum(1 for d in did if d.startswith("buy"))
        return (f"{len(did)} decisions, bought {bought}, locked {sum(1 for d in did if d.startswith('lock'))}, "
                f"rerolled {did.count('reroll')}, crates taken {did.count('take')}/{did.count('take') + did.count('recycle')}")

    def write(self, end_reason, wave, path=DECISIONS_FILE):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "end": end_reason, "wave": wave,
                                "decisions": self.decisions}) + "\n")
        self.decisions = []


class FrozenShop:
    """A trained shop brain that only plays (watch.py). Prints every pick so you can follow it."""

    def __init__(self, model, deterministic=True, verbose=True):
        self.model = model
        self.deterministic = deterministic
        self.verbose = verbose

    def decide(self, msg):
        obs, mask = read_question(msg)
        choice, _ = self.model.predict(obs, action_masks=mask, deterministic=self.deterministic)
        choice = int(choice)
        if self.verbose:
            info = msg.get("info", {})
            options = info.get("options", [])
            did = choice_name(msg["decision"], choice)
            target = options[choice % N_SLOTS] if (choice < 2 * N_SLOTS and choice % N_SLOTS < len(options)) else ""
            print(f"  [shop brain] wave {info.get('wave')} {msg['decision']}: {did} {target}")
        return choice

    def on_wave_cleared(self, wave, last_wave):
        pass

    def on_run_end(self, end_reason, wave):
        pass


class ShopBridge:
    """Connects the game (questions arrive in brotato_env.py, in the main thread) with ShopEnv
    (where MaskablePPO learns, in its own thread). Two queues: questions + rewards go to the brain,
    choices come back. Each question has a number so a late answer can never be used for the wrong one."""

    def __init__(self, verbose=True):
        self.to_brain = queue.Queue()
        self.to_game = queue.Queue()
        self.stopping = threading.Event()
        self.verbose = verbose
        self._reward = 0.0       # reward collected since the last question (waves cleared)
        self._question_id = 0
        self._tally = RunTally()

    # ----- called from the game side (brotato_env.py) -----

    def decide(self, msg):
        obs, mask = read_question(msg)
        self._question_id += 1
        qid = self._question_id
        self.to_brain.put(("question", qid, obs, mask, self._take_reward()))
        deadline = time.time() + ANSWER_TIMEOUT
        choice = None
        while time.time() < deadline and not self.stopping.is_set():
            try:
                answer_id, answer = self.to_game.get(timeout=0.5)
            except queue.Empty:
                continue
            if answer_id == qid:
                choice = answer
                break
        if choice is None or not mask[choice]:
            if not self.stopping.is_set():
                print("[shop] WARNING: no answer from the shop brain in time, picking at random")
            choice = random_choice(mask)
        self._tally.add(msg, choice)
        return choice

    def on_wave_cleared(self, wave, last_wave):
        self._reward += wave_reward(wave, last_wave)

    def on_run_end(self, end_reason, wave):
        truncated = end_reason not in ("died", "won")
        self.to_brain.put(("end", self._take_reward(), truncated))
        if self.verbose:
            print(f"[shop] run over: {self._tally.summary()}")
        self._tally.write(end_reason, wave)

    def stop(self):
        self.stopping.set()
        self.to_brain.put(("stop",))

    def _take_reward(self):
        reward, self._reward = self._reward, 0.0
        return reward


class ShopEnv(gym.Env):
    """The shop brain's view, for MaskablePPO. step() hands the choice back to the game and waits
    for the next question (or the end of the run). action_masks() says which choices are allowed."""

    metadata = {"render_modes": []}

    def __init__(self, bridge):
        super().__init__()
        self.observation_space = spaces.Box(low=-OBS_LIMIT, high=OBS_LIMIT, shape=(SHOP_OBS_SIZE,), dtype=np.float32)
        self.action_space = spaces.Discrete(N_CHOICES)
        self.bridge = bridge
        self._obs = np.zeros(SHOP_OBS_SIZE, dtype=np.float32)
        self._mask = np.ones(N_CHOICES, dtype=bool)
        self._qid = 0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        while True:
            item = self._next()
            if item[0] == "question":
                self._take_question(item)
                return self._obs, {}
            if item[0] == "stop":
                return self._obs, {}
            # "end" of a run without an open question: nothing to learn from, wait for the next one

    def step(self, action):
        self.bridge.to_game.put((self._qid, int(action)))
        item = self._next()
        if item[0] == "question":
            reward = item[4]
            self._take_question(item)
            return self._obs, reward, False, False, {}
        if item[0] == "end":
            _, reward, truncated = item
            return self._obs, reward, not truncated, truncated, {}
        return self._obs, 0.0, False, True, {}   # stop

    def action_masks(self):
        return self._mask

    def _take_question(self, item):
        _, self._qid, self._obs, self._mask, _ = item

    def _next(self):
        """Next item from the game. Once train.py is stopping, always "stop" (the library may call
        reset() or step() again after the first stop, and the stop item is only sent once)."""
        while True:
            if self.bridge.stopping.is_set() and self.bridge.to_brain.empty():
                return ("stop",)
            try:
                return self.bridge.to_brain.get(timeout=0.5)
            except queue.Empty:
                continue
