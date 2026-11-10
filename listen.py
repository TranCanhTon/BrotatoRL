import json
import math
import random
import socket
import sys
import time

from shop_env import random_choice, choice_name
from brotato_env import (HOST, PORT, OBS_SIZE, WALLS, HP, OWN_VELOCITY, ENEMIES, ENEMY_LEN, BULLETS,
                         MATERIALS, CONSUMABLES, RADAR_ENEMIES, RADAR_BULLETS, TIME_LEFT, BUILD)
PRINT_EVERY = 5   # print 1 of every 5 turns (every 0.5s game time)
HOLD_TURNS = 5    # keep each random move for 5 turns (0.5s) so the wandering is visible

SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0  # game speed, e.g. python listen.py 4

MOVE_NAMES = ["stay", "up", "down", "left", "right", "up left", "up right", "down left", "down right"]


def direction(dx, dy):
    # game y grows downward, so negative dy means up
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    names = ["right", "up right", "up", "up left", "left", "down left", "down", "down right"]
    return names[int((angle + 22.5) // 45) % 8]


def distance_text(dist, sight):
    return " far" if dist >= 1.0 else f"{dist * sight:4.0f}"


RADAR_NAMES = ["right", "down right", "down", "down left", "left", "up left", "up", "up right"]


def radar_text(values):
    """Busiest direction and how busy (0..1), e.g. "left 0.6"."""
    i = max(range(8), key=lambda k: values[k])
    return "empty" if values[i] <= 0 else f"{RADAR_NAMES[i]} {values[i]:.1f}"


def new_eyes(obs, info):
    """Second line: the numbers added in version 2, to check they look sensible."""
    e = obs[ENEMIES:ENEMIES + ENEMY_LEN]
    if e[2] < 0.999:
        enemy = (f"closest enemy speed {math.hypot(e[3], e[4]) * 800:4.0f} size {e[5] * 150:4.0f} "
                 f"boss {'yes' if e[6] > 0.5 else 'no '}")
    else:
        enemy = "no enemy" + " " * 30
    c = obs[CONSUMABLES:CONSUMABLES + 4]
    cons = f"pickup {distance_text(c[2], info['sight_range'])} {'crate' if c[3] > 0.5 else 'fruit'}" if c[2] < 0.999 else "pickup none      "
    b = obs[BUILD:BUILD + 14]
    build = (f"build hp {b[0] * 100:+.0f} armor {b[1] * 30:+.0f} dodge {b[2] * 60:+.0f} range {b[3] * 400:+.0f} "
             f"weapons {b[10] * 6:.0f} wave {b[11] * 20:.0f} danger {b[12] * 5:.0f} melee {b[13]:.1f}")
    return (f"     own speed {math.hypot(obs[OWN_VELOCITY], obs[OWN_VELOCITY + 1]) * 600:4.0f} | {enemy} | "
            f"radar enemies {radar_text(obs[RADAR_ENEMIES:RADAR_ENEMIES + 8])}, bullets "
            f"{radar_text(obs[RADAR_BULLETS:RADAR_BULLETS + 8])} | {cons} | {build} | "
            f"biggest enemy radius {info.get('max_enemy_radius', 0):.0f}")


def summary(obs, info):
    sight = info["sight_range"]
    walls = {"left": obs[WALLS], "right": obs[WALLS + 1], "top": obs[WALLS + 2], "bottom": obs[WALLS + 3]}
    wall_name = min(walls, key=walls.get)
    wall_units = walls[wall_name] * (info["arena_w"] if wall_name in ("left", "right") else info["arena_h"])

    if info["n_enemies"] > 0:
        enemy = (f"enemies {info['n_enemies']:3d}, closest {distance_text(obs[ENEMIES + 2], sight)} "
                 f"{direction(obs[ENEMIES], obs[ENEMIES + 1]):10s}")
    else:
        enemy = "enemies   0" + " " * 22

    if info["n_bullets"] > 0:
        b_dist = min(1.0, math.hypot(obs[BULLETS], obs[BULLETS + 1]))
        bullet = (f"bullets {info['n_bullets']:3d}, closest {distance_text(b_dist, sight)} "
                  f"{direction(obs[BULLETS], obs[BULLETS + 1]):10s}")
    else:
        bullet = "bullets   0" + " " * 22

    if info["n_materials"] > 0:
        mat = (f"materials {info['n_materials']:3d}, closest {distance_text(obs[MATERIALS + 2], sight)} "
               f"{direction(obs[MATERIALS], obs[MATERIALS + 1])}")
    else:
        mat = "materials   0"

    return (f"hp {obs[HP] * 100:3.0f}%  time {obs[TIME_LEFT] * 100:3.0f}%  "
            f"wall {wall_units:4.0f} {wall_name:6s} | {enemy} | {bullet} | {mat}\n" + new_eyes(obs, info))


def check(obs):
    problems = []
    if len(obs) != OBS_SIZE:
        problems.append(f"expected {OBS_SIZE} numbers, got {len(obs)}")
    for i, v in enumerate(obs):
        if not (-1.0 <= v <= 1.0):
            problems.append(f"number {i} out of range: {v}")
    return problems


def wave_report(wave, start, end, turns):
    real = max(1e-6, end - start)
    game = turns * 0.1
    return (f"=== Wave {wave} took {real:.1f} s real time for {game:.0f} s of game time "
            f"(actual speed {game / real:.1f}x) ===")


def read_lines(conn):
    # Reads one line at a time. Wakes up every 0.5s so Ctrl+C works on Windows.
    buffer = b""
    while True:
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            continue
        except ConnectionResetError:
            return
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line.decode("utf-8")


def wait_for_game(server):
    # Same idea: check every 0.5s instead of waiting forever, so Ctrl+C works.
    while True:
        try:
            return server.accept()
        except socket.timeout:
            continue


def serve():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)
    server.settimeout(0.5)

    while True:
        print("Waiting for the game on", HOST, PORT)
        conn, addr = wait_for_game(server)
        conn.settimeout(0.5)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"Game connected, speed {SPEED}x, aiming for {10 * SPEED:.0f} turns/s")
        turns = 0
        move = 0
        fastest_bullet = 0.0
        last_print_time = time.time()
        wave = None          # wave being timed
        wave_start = 0.0     # real time of its first turn
        wave_last = 0.0      # real time of its latest turn
        wave_turns = 0
        with conn:
            for line in read_lines(conn):
                d = json.loads(line)
                if "decision" in d:
                    # shop brain question (level up, crate, shop): answer at random so the game goes on
                    choice = random_choice(d["mask"])
                    conn.sendall((json.dumps({"choice": choice}) + "\n").encode("utf-8"))
                    info = d.get("info", {})
                    print(f"--- {d['decision']} at wave {info.get('wave')}: {len(d['obs'])} numbers, "
                          f"options {info.get('options')}, random pick: {choice_name(d['decision'], choice)} ---")
                    continue
                if "event" in d:
                    # wave end message from the mod, no answer needed
                    outcome = "DIED" if d["dead"] else ("left the wave" if d["aborted"] else "cleared")
                    print(f"--- wave {d['wave']} {outcome} ---")
                    continue
                obs, info = d["obs"], d["info"]

                if turns % HOLD_TURNS == 0:
                    move = random.randrange(len(MOVE_NAMES))
                conn.sendall((json.dumps({"action": move, "speed": SPEED}) + "\n").encode("utf-8"))

                for p in check(obs):
                    print("PROBLEM:", p)
                fastest_bullet = max(fastest_bullet, info["max_bullet_speed"])
                now = time.time()
                if info["wave"] != wave:
                    if wave is not None:
                        print(wave_report(wave, wave_start, wave_last, wave_turns))
                    wave, wave_start, wave_turns = info["wave"], now, 0
                wave_last = now
                wave_turns += 1
                turns += 1
                if turns == 1:
                    print(f"arena {info['arena_w']:.0f} x {info['arena_h']:.0f}, wave {info['wave']}, {len(obs)} numbers per turn")
                if turns % PRINT_EVERY == 0:
                    now = time.time()
                    rate = PRINT_EVERY / max(1e-6, now - last_print_time)
                    last_print_time = now
                    print(f"move {MOVE_NAMES[move]:10s} {rate:5.1f} turns/s | {summary(obs, info)}")
        if wave is not None:
            print(wave_report(wave, wave_start, wave_last, wave_turns))
        print(f"Game disconnected after {turns} turns. Fastest bullet seen: {fastest_bullet:.0f} units per second")


try:
    serve()
except KeyboardInterrupt:
    print("Stopped")
