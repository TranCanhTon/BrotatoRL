"""The shop brain: level ups, crates and the shop, decided by a second AI.

The mod asks a question every time something has to be picked between waves:
    {"decision": "shop" | "level_up" | "crate", "obs": [SHOP_OBS_SIZE numbers], "mask": [N_CHOICES true/false],
     "info": {"shop_obs_version", "wave", "gold", "options": [item ids], "prices": [...]}}
and waits for {"choice": n}. brotato_env.py receives these questions (they arrive in the middle of the
movement brain's turns) and hands them to a "shop" object from this file:

    RandomShop      picks a random allowed choice (run_random.py, rule_bot.py, listen.py)
    TeacherShop     Ton's stat priority (no item lists); records every pick in teacher_data.jsonl so the
                    shop brain can learn to copy them first (train.py ... teach, then pretrain_shop.py)
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

# the 44 stats in the obs, in the mod's order (SHOP_STATS in mod_main.gd)
SHOP_STAT_NAMES = [
    "stat_max_hp", "stat_hp_regeneration", "stat_lifesteal", "stat_percent_damage", "stat_melee_damage",
    "stat_ranged_damage", "stat_elemental_damage", "stat_attack_speed", "stat_crit_chance", "stat_engineering",
    "stat_range", "stat_armor", "stat_dodge", "stat_speed", "stat_luck", "stat_harvesting", "consumable_heal",
    "xp_gain", "pickup_range", "items_price", "explosion_damage", "explosion_size", "bounce", "piercing",
    "piercing_damage", "damage_against_bosses", "structure_attack_speed", "structure_range",
    "burning_cooldown_reduction", "burning_spread", "knockback", "chance_double_gold", "free_rerolls", "trees",
    "number_of_enemies", "enemy_speed", "reroll_price", "enemy_health", "enemy_damage", "lose_hp_per_second",
    "hp_start_next_wave", "hp_start_wave", "gold_drops", "harvesting_growth",
]
assert len(SHOP_STAT_NAMES) == N_STATS
STAT_INDEX = {name: i for i, name in enumerate(SHOP_STAT_NAMES)}

DECISIONS_FILE = "decisions.jsonl"
TEACHER_FILE = "teacher_data.jsonl"   # one line per run: every (numbers, allowed, teacher's pick, reward after it)   # one line per run: every question, what was offered, what it picked
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


# ---------- the teacher: Ton's stat priority (no item lists) ----------
# Level ups: the card whose best stat sits highest in TEACHER_PRIORITY. Shop: the affordable item whose best
# priority stat ranks highest and that lowers none of them and has no hidden downside (TEACHER_BAD), with the
# money rule (after a buy, leave once gold drops below what was just paid; reroll only if gold minus the reroll
# price still covers the target). Before leaving, lock wanted items it can't afford. Crates: take, except items
# with a hidden downside. SMGs come from the mod's rules. Items are judged only by their stats (try on numbers).
TEACHER_PRIORITY = ["max_hp", "percent_damage", "armor", "ranged_damage", "attack_speed", "dodge", "speed",
                    "crit_chance", "hp_regeneration"]
# hidden downsides, by stat: -1 = bad if the item lowers it, +1 = bad if it raises it
TEACHER_BAD = {"hp_start_next_wave": -1, "hp_start_wave": -1, "lose_hp_per_second": +1}


def option_view(obs, slot):
    """(present, locked, {stat name: change}) for one option, read from the obs numbers."""
    base = OPTIONS + slot * OPTION_SIZE
    header = obs[base:base + 8]
    changes = obs[base + 8:base + 8 + N_STATS]
    return header[0] > 0.5, header[5] > 0.5, changes


def has_bad_stat(changes):
    return any(changes[STAT_INDEX[stat]] * direction > 0 for stat, direction in TEACHER_BAD.items())


def teacher_rank(changes):
    """Position in TEACHER_PRIORITY of the item's best stat. 999 = don't buy (lowers a priority stat or has a
    hidden downside)."""
    if has_bad_stat(changes):
        return 999
    rank = 999
    for i, stat in enumerate(TEACHER_PRIORITY):
        value = changes[STAT_INDEX["stat_" + stat]]
        if value < 0:
            return 999
        if value > 0 and i < rank:
            rank = i
    return rank


class TeacherShop:
    """Answers with Ton's old rules and records every pick for pretrain_shop.py."""

    def __init__(self, path=TEACHER_FILE, verbose=True):
        self.path = path
        self.verbose = verbose
        self._samples = []      # this run: {"obs", "mask", "action", "reward"}
        self._reward = 0.0      # waves cleared since the last pick
        self._visit = None      # (wave) of the current shop visit, for the money rule
        self._last_buy = 0
        self._tally = RunTally()

    def decide(self, msg):
        obs, mask = read_question(msg)
        kind = msg["decision"]
        info = msg.get("info", {})
        if kind == "level_up":
            choice = self._level_up(obs, mask)
        elif kind == "crate":
            choice = 1 if (has_bad_stat(option_view(obs, 0)[2]) and mask[1]) else 0
        else:
            choice = self._shop(obs, mask, info)
        if not mask[choice]:
            choice = random_choice(mask)
        if self._samples:
            self._samples[-1]["reward"] += self._reward
        self._reward = 0.0
        self._samples.append({"obs": [round(float(x), 4) for x in obs], "mask": [bool(m) for m in mask],
                              "action": int(choice), "reward": 0.0})
        self._tally.add(msg, choice)
        return choice

    def _level_up(self, obs, mask):
        for stat in TEACHER_PRIORITY:
            for slot in range(N_SLOTS):
                present, _, changes = option_view(obs, slot)
                if mask[slot] and present and changes[STAT_INDEX["stat_" + stat]] > 0:
                    return slot
        return next(i for i in range(N_SLOTS) if mask[i]) if any(mask[:N_SLOTS]) else 0

    def _shop(self, obs, mask, info):
        wave = info.get("wave")
        if self._visit != wave:          # new shop visit
            self._visit = wave
            self._last_buy = 0
        ids = info.get("options", [""] * N_SLOTS)
        prices = info.get("prices", [0] * N_SLOTS)
        gold = info.get("gold", 0) or 0

        # 1. money rule: after a buy, stop once gold drops below what was just paid
        if self._last_buy > 0 and gold < self._last_buy:
            return self._leave(obs, mask, ids, prices, gold)
        # 2. best affordable item with priority stats
        best, best_rank = None, 999
        for slot in range(N_SLOTS):
            if mask[slot] and ids[slot].startswith("item_"):
                rank = teacher_rank(option_view(obs, slot)[2])
                if rank < best_rank:
                    best, best_rank = slot, rank
        if best is not None:
            self._last_buy = prices[best]
            return best
        # 3. reroll if it still leaves enough for the target
        target = self._last_buy if self._last_buy > 0 else (info.get("smg_price") or 0)
        if mask[CHOICE_REROLL] and gold - (info.get("reroll_price") or 0) >= target:
            return CHOICE_REROLL
        return self._leave(obs, mask, ids, prices, gold)

    def _leave(self, obs, mask, ids, prices, gold):
        """Lock one wanted item it can't afford (one per question), otherwise leave."""
        for slot in range(N_SLOTS):
            present, locked, changes = option_view(obs, slot)
            if not present or locked or not ids[slot].startswith("item_") or gold >= prices[slot]:
                continue
            wanted = teacher_rank(changes) < 999
            if wanted and mask[N_SLOTS + slot]:
                return N_SLOTS + slot
        return CHOICE_LEAVE

    def on_wave_cleared(self, wave, last_wave):
        self._reward += wave_reward(wave, last_wave)

    def on_run_end(self, end_reason, wave):
        if self._samples:
            self._samples[-1]["reward"] += self._reward
        self._reward = 0.0
        if self._samples:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "end": end_reason, "wave": wave,
                                    "cut_short": end_reason not in ("died", "won"), "samples": self._samples}) + "\n")
        if self.verbose:
            print(f"[teacher] run over: {self._tally.summary()}, {len(self._samples)} picks recorded")
        self._samples = []
        self._tally.write(end_reason, wave)
