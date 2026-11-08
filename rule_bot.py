"""Phase 3 baseline: a hand written bot that plays waves through BrotatoEnv. It does not learn.

Every turn (0.1 s of game time) it tries all 9 moves in its head: "if I walk this way for a
moment, where do I end up, and how dangerous is that spot?" Then it picks the safest move.
Danger comes from enemies close to that spot, bullets that would hit on the way, and walls.
Materials and the middle of the arena give a small bonus, so it collects when it is safe.

Its score (average wave and reward) is the bar the trained AI has to beat.

Usage:  python rule_bot.py [minutes] [speed]
        python rule_bot.py 30 4
"""

import math
import sys
import time

from brotato_env import BrotatoEnv
from run_random import print_report, print_summary

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

# ---------- what the numbers mean (layout comes from brotato_env.py, same as the mod sends) ----------
from brotato_env import (POS, ENEMIES, N_ENEMIES, ENEMY_LEN, BULLETS, N_BULLETS, BULLET_LEN,
                         MATERIALS, N_MATERIALS, SIGHT_RANGE)
BULLET_SPEED_MAX = 600.0   # bullet velocities are divided by this

# move number -> direction. 0 stay, 1 up, 2 down, 3 left, 4 right, 5 up left, 6 up right, 7 down left, 8 down right
D = 1 / math.sqrt(2)       # the game makes diagonals the same speed as straight moves
MOVES = [(0, 0), (0, -1), (0, 1), (-1, 0), (1, 0), (-D, -D), (D, -D), (-D, D), (D, D)]

# ---------- knobs (tune these if the bot plays badly) ----------
PLAYER_SPEED = 400.0       # rough game units per second the potato walks
LOOKAHEAD = 0.3            # seconds ahead the bot imagines for each move
ENEMY_SPEED = 200.0        # rough speed enemies walk toward you (so standing still gets riskier)
ENEMY_RADIUS = 250.0       # enemies farther than this from a spot don't count
ENEMY_WEIGHT = 1.0
BULLET_RADIUS = 50.0       # a bullet passing closer than this counts as a hit
BULLET_TIME = 0.5          # seconds of bullet flight checked
BULLET_WEIGHT = 3.0        # bullets matter more than enemies
WALL_MARGIN = 100.0        # game units from a wall where it starts to count
WALL_WEIGHT = 0.5
MATERIAL_WEIGHT = 0.3      # pull toward the closest material (weaker than danger)
CENTER_WEIGHT = 0.05       # tiny pull to the middle, so it doesn't idle in corners


def read_things(obs):
    """Turn the numbers into lists of (x, y) offsets in game units, empty slots dropped.
    The bot only uses enemy position, it ignores the newer enemy speed / size / boss numbers."""
    enemies, bullets, materials = [], [], []
    for i in range(N_ENEMIES):
        dx, dy, dist = obs[ENEMIES + ENEMY_LEN * i: ENEMIES + ENEMY_LEN * i + 3]
        if dist < 0.999:   # empty slot is 0, 0, 1
            enemies.append((dx * SIGHT_RANGE, dy * SIGHT_RANGE))
    for i in range(N_BULLETS):
        dx, dy, vx, vy = obs[BULLETS + BULLET_LEN * i: BULLETS + BULLET_LEN * i + 4]
        if dx or dy or vx or vy:   # empty slot is all zeros
            bullets.append((dx * SIGHT_RANGE, dy * SIGHT_RANGE,
                            vx * BULLET_SPEED_MAX, vy * BULLET_SPEED_MAX))
    for i in range(N_MATERIALS):
        dx, dy, dist = obs[MATERIALS + 3 * i: MATERIALS + 3 * i + 3]
        if dist < 0.999:
            materials.append((dx * SIGHT_RANGE, dy * SIGHT_RANGE))
    return enemies, bullets, materials


def enemy_danger(spot, enemies):
    """How crowded a spot will be after LOOKAHEAD seconds. Enemies are assumed to walk toward
    where you are now. Each enemy adds more the closer it is, nothing past ENEMY_RADIUS."""
    danger = 0.0
    for ex, ey in enemies:
        dist_now = math.hypot(ex, ey)
        walk = min(dist_now, ENEMY_SPEED * LOOKAHEAD)
        if dist_now > 0:
            ex, ey = ex - ex / dist_now * walk, ey - ey / dist_now * walk
        r = math.hypot(ex - spot[0], ey - spot[1])
        if r < ENEMY_RADIUS:
            danger += ENEMY_WEIGHT * (1 - r / ENEMY_RADIUS) ** 2
    return danger


def bullet_danger(move, bullets):
    """Walk this way for BULLET_TIME seconds: how badly does a bullet hit on the way?
    Checks every 0.05 s where you and each bullet would be. Earlier hits count more."""
    mx, my = MOVES[move]
    danger = 0.0
    for bx, by, vx, vy in bullets:
        worst = 0.0
        steps = 10
        for k in range(1, steps + 1):
            t = BULLET_TIME * k / steps
            px, py = mx * PLAYER_SPEED * t, my * PLAYER_SPEED * t
            r = math.hypot(bx + vx * t - px, by + vy * t - py)
            if r < BULLET_RADIUS:
                worst = max(worst, (1 - r / BULLET_RADIUS) * (1 - 0.5 * t / BULLET_TIME))
        danger += BULLET_WEIGHT * worst
    return danger


def wall_danger(spot_x, spot_y, arena_w, arena_h):
    """How close a spot is to the walls. Corners count two walls."""
    danger = 0.0
    for d in (spot_x, arena_w - spot_x, spot_y, arena_h - spot_y):
        if d < WALL_MARGIN:
            danger += WALL_WEIGHT * (1 - max(0.0, d) / WALL_MARGIN) ** 2
    return danger


def score_move(move, things, pos, arena):
    """Higher is better. Imagined spot after LOOKAHEAD seconds, minus danger, plus small bonuses."""
    enemies, bullets, materials = things
    mx, my = MOVES[move]
    step = PLAYER_SPEED * LOOKAHEAD
    spot = (mx * step, my * step)                      # offset from where you are now
    world_x, world_y = pos[0] + spot[0], pos[1] + spot[1]

    score = -enemy_danger(spot, enemies)
    score -= bullet_danger(move, bullets)
    score -= wall_danger(world_x, world_y, *arena)

    if materials:   # did this move get you closer to the closest material?
        mat_x, mat_y = materials[0]
        before = math.hypot(mat_x, mat_y)
        after = math.hypot(mat_x - spot[0], mat_y - spot[1])
        score += MATERIAL_WEIGHT * (before - after) / step

    center_x, center_y = arena[0] / 2, arena[1] / 2
    before = math.hypot(center_x - pos[0], center_y - pos[1])
    after = math.hypot(center_x - world_x, center_y - world_y)
    score += CENTER_WEIGHT * (before - after) / step
    return score


def decide(obs, arena_w, arena_h):
    """The bot's brain: the numbers in, a move 0..8 out."""
    things = read_things(obs)
    pos = (obs[POS] * arena_w, obs[POS + 1] * arena_h)
    arena = (arena_w, arena_h)
    return max(range(len(MOVES)), key=lambda m: score_move(m, things, pos, arena))


def play_one_run(env):
    obs, info = env.reset()
    run_start = time.time()
    total_reward, turns = 0.0, 0
    while True:
        move = decide(obs, info["arena_w"], info["arena_h"])
        obs, reward, terminated, truncated, info = env.step(move)
        total_reward += reward
        turns += 1
        if terminated or truncated:
            return (info.get("end_reason"), info.get("end_wave"), total_reward, turns,
                    time.time() - run_start, info.get("report"))


def main():
    env = BrotatoEnv(speed=SPEED, verbose=False)
    print(f"Rule bot runs for {MINUTES:.0f} minutes at {SPEED:.0f}x. Start Brotato now, then don't touch anything.")

    runs = []
    start = None
    try:
        while start is None or time.time() - start < MINUTES * 60:
            result = play_one_run(env)
            if start is None:
                start = time.time() - result[4]   # clock starts at the first run's first turn
            runs.append(result)
            why, wave, reward, turns, seconds, report = result
            print(f"run {len(runs):3d}: {why:17s} wave {wave:2d} | reward {reward:7.1f} | "
                  f"{turns:5d} turns | {seconds:5.0f} s | total {(time.time() - start) / 60:5.1f} min")
            print_report(report)
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        env.close()

    if runs:
        print_summary(runs, (time.time() - start) / 60)


if __name__ == "__main__":
    main()
