"""Brotato as a Gymnasium environment (the "adapter" between the AI library and the game).

The game, with the BrotatoAI mod, connects to this script over TCP on 127.0.0.1:9876.
Every 0.1 s of game time the mod sends OBS_SIZE numbers (layout below) and waits for a move.
When a wave ends the mod sends a "wave_end" message (died, cleared or aborted).
The mod's autopilot handles menus, level ups, shop and restarts by itself.

One episode = one run: from wave 1 until the player dies or clears LAST_WAVE.

Between waves the mod asks the shop brain questions (level ups, crates, shop). They arrive here in the
middle of a step() and are handed to the "shop" object (see shop_env.py), which answers them.
Without a shop object the answers are random.
"""

import json
import socket
from collections import deque

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from shop_env import RandomShop

HOST = "127.0.0.1"
PORT = 9876
N_MOVES = 9

# ---------- what the numbers mean (must match the mod, version 2) ----------
OBS_VERSION = 2
POS = 0                            # x, y as a fraction of the arena
WALLS = 2                          # left, right, top, bottom
HP = 6
OWN_VELOCITY = 7                   # vx, vy
ENEMIES, N_ENEMIES, ENEMY_LEN = 9, 8, 7      # dx, dy, dist, vx, vy, size, boss
BULLETS, N_BULLETS, BULLET_LEN = 65, 8, 4    # dx, dy, vx, vy
MATERIALS, N_MATERIALS = 97, 3               # dx, dy, dist
CONSUMABLES, N_CONSUMABLES = 106, 2          # dx, dy, dist, crate
RADAR_ENEMIES = 114                # 8 directions: right, down right, down, down left, left, up left, up, up right
RADAR_BULLETS = 122
TIME_LEFT = 130
BUILD = 131                        # 10 stats, weapons, wave, danger, melee share
SPARE, N_SPARE = 145, 16
OBS_SIZE = 161
SIGHT_RANGE = 600.0                # offsets and distances are divided by this

# ---------- rewards (starting values, tune during training) ----------
REWARD_ALIVE = 0.01          # every turn (0.1 s of game time) alive
REWARD_HIT = -5.0            # times the fraction of max HP lost
REWARD_MATERIAL = 0.05       # per gold gained during a wave (1 material = 1 gold)
REWARD_WAVE_CLEARED = 3.0
REWARD_RUN_WON = 10.0        # extra for clearing the last wave
REWARD_DIED = -20.0          # was -5: dying has to hurt more than one wave of materials is worth
REWARD_BOSS_WAVE = 10.0      # extra for clearing a boss wave
BOSS_WAVES = (11, 15, 18, 20)
REWARD_WALL = -0.02          # per turn while near a wall
WALL_DISTANCE = 40           # game units from a wall that count as "near"
DEATH_LOG_TURNS = 50         # remember the last 5 s of every run for the death log


class BrotatoEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, speed=20.0, verbose=True, watch=False, shop=None):
        super().__init__()
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)
        self.action_space = spaces.Discrete(N_MOVES)
        self.speed = float(speed)
        self.verbose = verbose
        self.watch = watch   # True = watch.py: the mod clicks slower in menus and highlights what it picks
        self.shop = shop if shop is not None else RandomShop()   # answers the shop brain questions

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((HOST, PORT))
        self._server.listen(1)
        self._server.settimeout(0.5)   # wake up every 0.5 s so Ctrl+C works on Windows
        self._conn = None
        self._buffer = b""

        self._last_obs = np.zeros(OBS_SIZE, dtype=np.float32)
        self._last_info = {}
        self._wave = 0
        self._run_over = True
        self._report = None   # what the autopilot did this run, from the mod
        self._stats = {}      # what happened this run, broken down (for graphs, not seen by the AI)
        self._recent = deque(maxlen=DEATH_LOG_TURNS)   # last turns of the run, for the death log

    # ---------- the two buttons the AI library presses ----------

    def reset(self, seed=None, options=None):
        """Start of a run: wait for the first turn the game sends, return what the AI sees."""
        super().reset(seed=seed)
        msg = self._next_turn()
        self._check_version(msg)
        self._recent.clear()
        self._run_over = False
        self._wave = msg["info"]["wave"]
        self._stats = {
            "turns": 0,
            "materials": 0,        # gold picked up during waves
            "damage_taken": 0.0,   # HP lost, in max HPs (1.0 = lost a full health bar in total)
            "wall_turns": 0,       # turns spent near a wall
            "waves_cleared": 0,
            # the reward split into its parts (they add up to the run's total reward)
            "reward_alive": 0.0, "reward_hit": 0.0, "reward_material": 0.0, "reward_wall": 0.0,
            "reward_wave": 0.0, "reward_boss": 0.0, "reward_won": 0.0, "reward_died": 0.0,
        }
        self._remember(msg)
        return self._last_obs, dict(self._last_info)

    def step(self, action):
        """Send one move, wait for the result. Returns obs, reward, terminated, truncated, info."""
        if self._run_over:
            raise RuntimeError("the run is over, call reset() first")

        self._send_move(action)
        reward = 0.0
        self._recent.append(self._turn_summary(action))

        while True:
            msg = self._read_message()

            # a shop brain question (level up, crate, shop): answer it, keep waiting for the next turn.
            # Checked first: questions have an "obs" too (the shop brain's numbers)
            if "decision" in msg:
                self._answer(msg)
                continue

            # a normal turn: the game moved on 0.1 s
            if "obs" in msg:
                info = msg["info"]
                if info["wave"] == self._wave:
                    reward += self._hit_and_material_reward(self._last_info, info)
                else:
                    # first turn of a new wave: HP and gold were changed by the shop, don't count them
                    self._wave = info["wave"]
                wall = self._wall_penalty(msg["obs"], info)
                reward += REWARD_ALIVE + wall
                self._stats["turns"] += 1
                self._stats["reward_alive"] += REWARD_ALIVE
                self._stats["reward_wall"] += wall
                if wall:
                    self._stats["wall_turns"] += 1
                self._remember(msg)
                return self._last_obs, reward, False, False, dict(info)

            # the wave ended
            if msg.get("event") == "wave_end":
                self._report = msg.get("report")   # only on the last wave of a run
                if msg["dead"]:
                    self._stats["reward_died"] += REWARD_DIED
                    return self._end_run(reward + REWARD_DIED, "died", msg["wave"], terminated=True)
                if msg["aborted"]:
                    return self._end_run(reward, "left the wave", msg["wave"], terminated=False)
                self.shop.on_wave_cleared(msg["wave"], msg["last_wave"])
                reward += REWARD_WAVE_CLEARED
                self._stats["waves_cleared"] += 1
                self._stats["reward_wave"] += REWARD_WAVE_CLEARED
                if msg["wave"] in BOSS_WAVES:
                    reward += REWARD_BOSS_WAVE
                    self._stats["reward_boss"] += REWARD_BOSS_WAVE
                if msg["wave"] >= msg["last_wave"]:
                    self._stats["reward_won"] += REWARD_RUN_WON
                    return self._end_run(reward + REWARD_RUN_WON, "won", msg["wave"], terminated=True)
                self._log(f"wave {msg['wave']} cleared")
                continue   # level ups and shop happen now, then the next wave's first turn arrives

            if msg.get("event") == "disconnected":
                return self._end_run(reward, "game disconnected", self._wave, terminated=False)

    def close(self):
        if self._conn is not None:
            self._conn.close()
        self._server.close()

    # ---------- rewards ----------

    def _hit_and_material_reward(self, prev, now):
        reward = 0.0
        hp_lost = prev["hp"] - now["hp"]
        if hp_lost > 0:
            hit = REWARD_HIT * hp_lost / max(1, now["max_hp"])
            reward += hit
            self._stats["damage_taken"] += hp_lost / max(1, now["max_hp"])
            self._stats["reward_hit"] += hit
        gold_gained = now["gold"] - prev["gold"]
        if gold_gained > 0:
            reward += REWARD_MATERIAL * gold_gained
            self._stats["materials"] += gold_gained
            self._stats["reward_material"] += REWARD_MATERIAL * gold_gained
        return reward

    @staticmethod
    def _wall_penalty(obs, info):
        left, right, top, bottom = obs[WALLS:WALLS + 4]
        w, h = info["arena_w"], info["arena_h"]
        nearest = min(left * w, right * w, top * h, bottom * h)
        return REWARD_WALL if nearest < WALL_DISTANCE else 0.0

    # ---------- run bookkeeping ----------

    def _remember(self, msg):
        self._last_obs = np.asarray(msg["obs"], dtype=np.float32)
        self._last_info = msg["info"]

    @staticmethod
    def _check_version(msg):
        """Stops with a clear message if the mod sends a different layout than this file expects."""
        version = msg["info"].get("obs_version", 1)
        if version != OBS_VERSION or len(msg["obs"]) != OBS_SIZE:
            raise RuntimeError(
                f"The mod sends layout version {version} with {len(msg['obs'])} numbers, but brotato_env.py "
                f"expects version {OBS_VERSION} with {OBS_SIZE}. Close Brotato, run python make_test_mod.py, start it again.")

    def _turn_summary(self, action):
        """What the AI saw and did this turn, short. Kept for the last DEATH_LOG_TURNS turns of a run."""
        obs, info = self._last_obs, self._last_info
        enemy = obs[ENEMIES:ENEMIES + N_ENEMIES * ENEMY_LEN].reshape(N_ENEMIES, ENEMY_LEN)
        bullet = obs[BULLETS:BULLETS + N_BULLETS * BULLET_LEN].reshape(N_BULLETS, BULLET_LEN)
        real = enemy[:, 2] < 0.999                  # empty enemy slots have dist 1
        bosses = enemy[real & (enemy[:, 6] > 0.5)]
        bullets = bullet[np.any(bullet != 0, axis=1)]
        closest = enemy[0] if real[0] else None
        return {
            "move": int(action),
            "hp": info.get("hp"),
            # distances in game units, None if nothing seen
            "enemy": round(float(closest[2]) * SIGHT_RANGE) if closest is not None else None,
            "enemy_speed": round(float(np.hypot(closest[3], closest[4])) * 800) if closest is not None else None,
            "enemy_size": round(float(closest[5]) * 150) if closest is not None else None,
            "boss": round(float(bosses[:, 2].min()) * SIGHT_RANGE) if len(bosses) else None,
            "bullet": round(float(np.hypot(bullets[:, 0], bullets[:, 1]).min()) * SIGHT_RANGE) if len(bullets) else None,
            "enemies": info.get("n_enemies"),
            "bullets": info.get("n_bullets"),
        }

    def _end_run(self, reward, why, wave, terminated):
        # terminated = real end of a run (died or won). truncated = cut short (quit, disconnect).
        self._run_over = True
        self._log(f"run over: {why} at wave {wave}")
        self.shop.on_run_end(why if why in ("died", "won") else "cut short", wave)
        info = dict(self._last_info)
        info.update({"end_reason": why, "end_wave": wave, "report": self._report, "stats": dict(self._stats),
                     "last_turns": list(self._recent)})
        self._report = None
        return self._last_obs, reward, terminated, not terminated, info

    # ---------- connection to the game ----------

    def _log(self, *args):
        if self.verbose:
            print("[BrotatoEnv]", *args)

    def _wait_for_game(self):
        self._log(f"waiting for the game on {HOST}:{PORT}")
        while True:
            try:
                conn, _ = self._server.accept()
                break
            except socket.timeout:
                continue
        conn.settimeout(0.5)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._conn = conn
        self._buffer = b""
        self._log("game connected")

    def _read_message(self):
        """Next message from the game. Returns {"event": "disconnected"} if the game closed."""
        if self._conn is None:
            self._wait_for_game()
        while b"\n" not in self._buffer:
            try:
                chunk = self._conn.recv(65536)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionAbortedError):
                chunk = b""
            if not chunk:
                self._log("game disconnected")
                self._conn.close()
                self._conn = None
                return {"event": "disconnected"}
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def _next_turn(self):
        """Skip anything that isn't a turn (wave ends, disconnects) until the game sends a turn.
        Shop brain questions are still answered (the game waits for them)."""
        while True:
            msg = self._read_message()
            if "decision" in msg:
                self._answer(msg)
            elif "obs" in msg:
                return msg

    def _answer(self, msg):
        choice = int(self.shop.decide(msg))
        try:
            self._conn.sendall((json.dumps({"choice": choice}) + "\n").encode("utf-8"))
        except (OSError, AttributeError):
            self._conn = None

    def _send_move(self, action):
        msg = json.dumps({"action": int(action), "speed": self.speed, "watch": self.watch}) + "\n"
        try:
            self._conn.sendall(msg.encode("utf-8"))
        except (OSError, AttributeError):
            self._conn = None
