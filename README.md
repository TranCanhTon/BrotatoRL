# BrotatoRL

An AI that learns to play the real Steam version of **Brotato** with reinforcement learning, running for free on a normal laptop.

A Godot mod inside the game sends what is happening on screen to Python, Python answers with a move, and the mod plays the rest of the game by itself (menus, level ups, shop, restarts) so training can run overnight with nobody touching anything.

**Current target:** clear wave 12 (first boss) reliably. Later wave 20.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 1. Setup | Python env, unpack game code, test mod, find the game variables | Done |
| 2. Connection | Mod and Python talk, Python controls movement, speed up, autopilot, Gymnasium env | Built. Final 30 minute test left |
| 3. Brain | Rule bot baseline, PPO agent | Not started |
| 4. Training | Rewards, train, watch, tune | Not started |
| 5. Shop and higher waves | Smarter shop, wave 12 then 20 | Basic shop rules already in the autopilot |

Already working: with random moves, the autopilot plays full runs back to back and has cleared wave 10 (the old last wave).

Next: run `run_random.py` for 30 minutes unattended. Phase 2 is done when its summary shows 0 runs that ended some other way.

## How it works

```
Brotato (Godot 3)                          Python
mod_main.gd  ── 73 numbers (JSON line) ──▶ listen.py / brotato_env.py
             ◀── {"action": n, "speed": s} ─
```

1. Every **0.1 s of game time** the mod freezes the game and sends 73 numbers describing the situation.
2. Python picks one of 9 moves and sends it back, together with the game speed.
3. The mod holds that move for the next 0.1 s and the game continues.

This "taking turns" design (lockstep) guarantees every move answers exactly the situation the AI saw, even when Python is slow. If Python does not answer within 20 s (long enough for the AI to pause and learn during training), the mod lets go of the keys, returns to normal speed and keeps trying to reconnect.

When a wave ends, the mod sends one extra message that Python does not answer:

```
{"event": "wave_end", "wave": 3, "dead": false, "aborted": false, "last_wave": 10}
```

`dead` means the player died, `aborted` means the wave was left without ending (quit to menu, game closed), otherwise the wave was cleared. On the last wave of a run (death or wave 12 cleared) the message also carries a `report`: a log of every autopilot action this run (wave, action, item id, price, item stats) and the character's final stats.

The connection is plain TCP on `127.0.0.1:9876` (your own laptop only), one JSON object per line.

## What the AI sees (73 numbers)

All numbers are scaled to 0..1 or minus 1..1. Empty slots are filled so they mean "nothing there".

| Index | What | Count | Scaling |
| --- | --- | --- | --- |
| 0 to 1 | Player position in the arena | 2 | fraction of arena size |
| 2 to 5 | Distance to left, right, top, bottom wall | 4 | fraction of arena size |
| 6 | HP | 1 | HP / max HP |
| 7 to 30 | 8 closest enemies: dx, dy, distance | 24 | divided by sight range 600, empty = 0, 0, 1 |
| 31 to 62 | 8 closest enemy bullets: dx, dy, vx, vy | 32 | position / 600, speed / 600, empty = zeros |
| 63 to 71 | 3 closest materials: dx, dy, distance | 9 | divided by sight range 600, empty = 0, 0, 1 |
| 72 | Time left in the wave | 1 | time left / wave length |

Notes:
* The sight range of 600 keeps close enemies easy to tell apart. Anything further reads as "far" (1).
* 600 is also the fastest enemy bullet measured (waves 3 to 6 and wave 81).
* The mod also sends a small `info` block (HP, gold, enemy count, arena size and so on). The AI does not see it. `brotato_env.py` uses HP and gold from it to work out rewards.

## What the AI can do (9 moves)

| Number | Move |
| --- | --- |
| 0 | Stay |
| 1 to 4 | Up, down, left, right |
| 5 to 8 | Up left, up right, down left, down right |

Moves press the game's own movement actions (`button_move_left` and so on). The game's **"mouse only" setting must be off**.

## Speed

Python sends the wanted speed with every move (`python listen.py 20` means 20x). The mod raises the game speed **and** the number of physics ticks per second together, so every tick is still the normal 1/60 s of game time. That keeps collisions accurate (bullets cannot skip through the player) and keeps one AI decision every 0.1 s of game time.

Measured on the development laptop (GTX 1650):

| Asked | Actual, waves 1 to 6 |
| --- | --- |
| 8x | 8x |
| 12x | 11 to 12x |
| 20x | 15 to 16x |

So about 15x is the limit in early waves, roughly 150 decisions per second, or 1 million decisions in about 2 hours. Speed goes back to 1x in menus and when Python disconnects.

## Autopilot

The autopilot plays everything that is not a wave. It only runs while Python is connected, so with Python closed the game plays completely normally. It "clicks" by calling the game's own button functions, so the game behaves exactly as if a person clicked.

All settings live in one block near the top of `mod/mod_main.gd`.

### Run setup
* Character: **Well Rounded**
* Weapon: **SMG**
* Danger: **1**
* The run ends on death or after clearing **wave 12** (the first boss; wave 10 was too easy), then a new run starts with the same setup.

### Level ups
Picks the first stat offered from this priority list, otherwise the first option:

1. Max HP
2. Dodge
3. Armor
4. Ranged damage
5. Damage %
6. Attack speed
7. Movement speed

### Crates (item boxes)
Takes items. Recycles weapons, so weapons only come from the shop rule below.

### Shop
One action every 0.25 s, in this order:

1. **After wave 12:** restart the run.
2. **All 6 slots full and an SMG in the shop:**
   * if it can afford the SMG: merge two identical SMGs (same tier) into one higher tier SMG, which frees a slot, then buy the SMG
   * if it cannot afford it: lock the SMG and leave the shop right away
3. **Buy:**
   * Under **6 weapons**: buy the cheapest SMG it can afford.
   * At **6 weapons**: buy items that give a stat from the priority list above (Max HP, dodge, armor, ranged damage, damage, attack speed, movement speed). Items that lower any of those stats are skipped. The item whose best stat is highest in the list wins.
4. **Money rule (per shop visit):**
   * After buying something, if the money left is less than what was just paid, leave the shop. Prices go up every wave, so it does not waste money on rerolls it cannot follow up on.
   * Only reroll if, after paying for the reroll, there is still enough money for that same price.
   * Before anything is bought in this visit, "that price" is what a basic SMG costs at the current wave (the game's own price formula).
5. **Lock before leaving:** anything it would buy but cannot afford gets locked (the game's own lock button), so it is still in the shop next round: SMGs always, and at 6 weapons also items with a priority stat.
6. Otherwise leave the shop.

### Death and restarts
* "Retry wave" screen: ends the run instead of retrying.
* End of run screen: presses Restart (same character, weapon and danger).

## Planned for the AI (Phase 3 and 4)

Right now Python picks **random** moves, so the character hugs walls and ignores materials. Teaching it to collect materials and avoid corners is the AI's job, through rewards. These are already calculated in `brotato_env.py`:

| Event | Reward |
| --- | --- |
| Every 0.1 s alive | +0.01 |
| Getting hit | minus 5 × (HP lost / max HP) |
| Picking up a material (gold went up during a wave) | +0.05 per gold |
| Clearing a wave | +3 |
| Clearing wave 12 | +10, run ends |
| Dying | minus 5, run ends |
| Closer than 40 units to a wall | minus 0.02 per turn |

These are starting values and will be tuned during training. The AI will be PPO from Stable Baselines3 with a small network (2 layers of 64). Plugging it in is meant to be this short:

```python
from stable_baselines3 import PPO
from brotato_env import BrotatoEnv

env = BrotatoEnv(speed=20)
model = PPO("MlpPolicy", env)
model.learn(total_timesteps=1_000_000)
```

What the AI learns lives in the network's weights, so it keeps its skill from run to run even though every run (shop, drops, level ups) is random. Training saves the weights to a file so the next session continues where the last one stopped.

## Files

| File | What it does |
| --- | --- |
| `mod/mod_main.gd` | The Godot mod: observations, lockstep moves, speed, autopilot |
| `make_test_mod.py` | Packs the mod into the zip Steam Brotato loads (Workshop folder) |
| `brotato_env.py` | The adapter between the AI library and the game: Gymnasium `reset()` and `step()`, rewards, one episode per run |
| `run_random.py` | Phase 2 final test: random moves play runs through `brotato_env.py` for N minutes, prints one line per run, a run report (everything the autopilot bought, merged, picked on level ups and took from crates, with each item's stats, plus the final character stats) and a summary |
| `listen.py` | Debug tool that stands in for the AI: prints what the mod sends in readable form, answers with random moves, times each wave |
| `find_vars.py` | Searches the unpacked game code for the variables the mod needs |
| `mod-icon.png` | Workshop icon for the mod |

Not in this repo on purpose (see `.gitignore`): the decompiled game code (copyrighted), the Python venv and personal save backups.

## Setup

Tested with Brotato 1.2.0.1 (Default Public Version), mod loader 6.3.0, Windows, Python 3.10.

1. Python environment:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install stable-baselines3 gymnasium numpy tensorboard
   ```
2. **Mod loading:** Steam Brotato only loads mods from the Steam Workshop, not from the local `mods` folder. The mod is uploaded as a hidden Workshop item and subscribed to. `make_test_mod.py` overwrites the zip inside that Workshop item's folder (`steamapps/workshop/content/1942280/<item id>`). Adjust the paths at the top of the script for your machine.
3. In the game options, turn **"mouse only" off**.

## Running

1. Close Brotato (the mod only loads at game start).
2. `python make_test_mod.py`
3. One of:
   * `python run_random.py 30 20` : random runs for 30 minutes at 20x through the Gymnasium env
   * `python listen.py 20` : watch what the mod sends, at 20x
4. Start Brotato and don't touch anything.
5. Stop with Ctrl+C.

Game logs (useful when something breaks): `%APPDATA%\Brotato\logs\godot.log`. Every autopilot action is logged there with a `BrotatoAI autopilot:` prefix.

## Lessons learned

* **Never name Brotato classes in the mod** (`Shop`, `WeaponData`, `TitleScreen` and so on). Godot then loads those scripts while the mod starts, the shop script fails that early, the mod fails, and the game restarts itself with mods disabled ("Recovering from unexpected error in mod... Mods have been temporarily disabled"). The mod recognises screens by their script file path instead.
* **Don't read game objects after a wave ends.** The game deletes its bullet container when a wave ends. Reading it afterwards reads freed memory and can crash the game. The mod stops taking turns as soon as the game starts cleaning up.
* **Raising `Engine.time_scale` alone is not enough** in Godot 3. It makes each physics tick bigger instead of adding ticks, which caps decisions and makes collisions coarse. Raise `Engine.iterations_per_second` with it.
* **Ctrl+C on Windows** does not interrupt a Python socket that waits forever. `listen.py` waits in 0.5 s steps so Ctrl+C works.
