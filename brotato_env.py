"""Brotato as a Gymnasium environment (the "adapter" between the AI library and the game).

The game, with the BrotatoAI mod, connects to this script over TCP on 127.0.0.1:9876.
Every 0.1 s of game time the mod sends 73 numbers and waits for a move.
When a wave ends the mod sends a "wave_end" message (died, cleared or aborted).
The mod's autopilot handles menus, level ups, shop and restarts by itself.

One episode = one run: from wave 1 until the player dies or clears LAST_WAVE.
"""

import json
import socket

import gymnasium as gym
import numpy as np
from gymnasium import spaces

HOST = "127.0.0.1"
PORT = 9876
OBS_SIZE = 73
N_MOVES = 9

# ---------- rewards (starting values, tune during training) ----------
REWARD_ALIVE = 0.01          # every turn (0.1 s of game time) alive
REWARD_HIT = -5.0            # times the fraction of max HP lost
REWARD_MATERIAL = 0.05       # per gold gained during a wave (1 material = 1 gold)
REWARD_WAVE_CLEARED = 3.0
REWARD_RUN_WON = 10.0        # extra for clearing the last wave
REWARD_DIED = -5.0
REWARD_WALL = -0.02          # per turn while near a wall
WALL_DISTANCE = 40           # game units from a wall that count as "near"


class BrotatoEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, speed=20.0, verbose=True):
        super().__init__()
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(OBS_SIZE,), dtype=np.float32)
        self.action_space = spaces.Discrete(N_MOVES)
        self.speed = float(speed)
        self.verbose = verbose

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

    # ---------- the two buttons the AI library presses ----------

    def reset(self, seed=None, options=None):
        """Start of a run: wait for the first turn the game sends, return what the AI sees."""
        super().reset(seed=seed)
        msg = self._next_turn()
        self._run_over = False
        self._wave = msg["info"]["wave"]
        self._remember(msg)
        return self._last_obs, dict(self._last_info)

    def step(self, action):
        """Send one move, wait for the result. Returns obs, reward, terminated, truncated, info."""
        if self._run_over:
            raise RuntimeError("the run is over, call reset() first")

        self._send_move(action)
        reward = 0.0

        while True:
            msg = self._read_message()

            # a normal turn: the game moved on 0.1 s
            if "obs" in msg:
                info = msg["info"]
                if info["wave"] == self._wave:
                    reward += self._hit_and_material_reward(self._last_info, info)
                else:
                    # first turn of a new wave: HP and gold were changed by the shop, don't count them
                    self._wave = info["wave"]
                reward += REWARD_ALIVE + self._wall_penalty(msg["obs"], info)
                self._remember(msg)
                return self._last_obs, reward, False, False, dict(info)

            # the wave ended
            if msg.get("event") == "wave_end":
                if msg["dead"]:
                    return self._end_run(reward + REWARD_DIED, "died", msg["wave"], terminated=True)
                if msg["aborted"]:
                    return self._end_run(reward, "left the wave", msg["wave"], terminated=False)
                reward += REWARD_WAVE_CLEARED
                if msg["wave"] >= msg["last_wave"]:
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

    @staticmethod
    def _hit_and_material_reward(prev, now):
        reward = 0.0
        hp_lost = prev["hp"] - now["hp"]
        if hp_lost > 0:
            reward += REWARD_HIT * hp_lost / max(1, now["max_hp"])
        gold_gained = now["gold"] - prev["gold"]
        if gold_gained > 0:
            reward += REWARD_MATERIAL * gold_gained
        return reward

    @staticmethod
    def _wall_penalty(obs, info):
        left, right, top, bottom = obs[2:6]
        w, h = info["arena_w"], info["arena_h"]
        nearest = min(left * w, right * w, top * h, bottom * h)
        return REWARD_WALL if nearest < WALL_DISTANCE else 0.0

    # ---------- run bookkeeping ----------

    def _remember(self, msg):
        self._last_obs = np.asarray(msg["obs"], dtype=np.float32)
        self._last_info = msg["info"]

    def _end_run(self, reward, why, wave, terminated):
        # terminated = real end of a run (died or won). truncated = cut short (quit, disconnect).
        self._run_over = True
        self._log(f"run over: {why} at wave {wave}")
        info = dict(self._last_info)
        info.update({"end_reason": why, "end_wave": wave})
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
        """Skip anything that isn't a turn (wave ends, disconnects) until the game sends a turn."""
        while True:
            msg = self._read_message()
            if "obs" in msg:
                return msg

    def _send_move(self, action):
        msg = json.dumps({"action": int(action), "speed": self.speed}) + "\n"
        try:
            self._conn.sendall(msg.encode("utf-8"))
        except (OSError, AttributeError):
            self._conn = None
