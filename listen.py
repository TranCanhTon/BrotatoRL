import json
import math
import socket

HOST = "127.0.0.1"
PORT = 9876
PRINT_EVERY = 5  # print 1 of every 5 messages (every 0.5s game time)
OBS_SIZE = 73


def direction(dx, dy):
    # game y grows downward, so negative dy means up
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    names = ["right", "up right", "up", "up left", "left", "down left", "down", "down right"]
    return names[int((angle + 22.5) // 45) % 8]


def summary(obs, info):
    diag = info["diag"]
    walls = {"left": obs[2], "right": obs[3], "top": obs[4], "bottom": obs[5]}
    wall_name = min(walls, key=walls.get)
    wall_units = walls[wall_name] * (info["arena_w"] if wall_name in ("left", "right") else info["arena_h"])

    e_dx, e_dy, e_dist = obs[7], obs[8], obs[9]
    if info["n_enemies"] > 0:
        enemy = f"enemies {info['n_enemies']:3d}, closest {e_dist * diag:5.0f} {direction(e_dx, e_dy):10s}"
    else:
        enemy = "enemies   0" + " " * 23

    b_dx, b_dy = obs[31], obs[32]
    if info["n_bullets"] > 0:
        bullet = f"bullets {info['n_bullets']:3d}, closest {math.hypot(b_dx, b_dy) * diag:5.0f} {direction(b_dx, b_dy):10s}"
    else:
        bullet = "bullets   0" + " " * 23

    m_dx, m_dy, m_dist = obs[63], obs[64], obs[65]
    if info["n_materials"] > 0:
        mat = f"materials {info['n_materials']:3d}, closest {m_dist * diag:5.0f} {direction(m_dx, m_dy)}"
    else:
        mat = "materials   0"

    return (f"hp {obs[6] * 100:3.0f}%  time {obs[72] * 100:3.0f}%  "
            f"wall {wall_units:4.0f} {wall_name:6s} | {enemy} | {bullet} | {mat}")


def check(obs):
    problems = []
    if len(obs) != OBS_SIZE:
        problems.append(f"expected {OBS_SIZE} numbers, got {len(obs)}")
    for i, v in enumerate(obs):
        if not (-1.0 <= v <= 1.0):
            problems.append(f"number {i} out of range: {v}")
    return problems


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(1)

while True:
    print("Waiting for the game on", HOST, PORT)
    conn, addr = server.accept()
    print("Game connected")
    count = 0
    fastest_bullet = 0.0
    with conn, conn.makefile("r", encoding="utf-8") as lines:
        for line in lines:
            d = json.loads(line)
            obs, info = d["obs"], d["info"]
            for p in check(obs):
                print("PROBLEM:", p)
            fastest_bullet = max(fastest_bullet, info["max_bullet_speed"])
            count += 1
            if count == 1:
                print(f"arena {info['arena_w']:.0f} x {info['arena_h']:.0f}, wave {info['wave']}, {len(obs)} numbers per message")
            if count % PRINT_EVERY == 0:
                print(summary(obs, info))
    print(f"Game disconnected. Fastest bullet seen: {fastest_bullet:.0f} units per second")
