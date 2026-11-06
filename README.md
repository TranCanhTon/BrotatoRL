# BrotatoRL

An AI that learns to play the real Steam version of **Brotato** with reinforcement learning, running for free on a normal laptop.

A Godot mod inside the game sends what is happening on screen to Python, Python answers with a move, and the mod plays the rest of the game by itself (menus, level ups, shop, restarts) so training can run overnight with nobody touching anything.

**Current target:** clear wave 10 reliably. Later wave 20.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 1. Setup | Python env, unpack game code, test mod, find the game variables | Done |
| 2. Connection | Mod and Python talk, Python controls movement, speed up, autopilot | Almost done (brotato_env.py left) |
| 3. Brain | Rule bot baseline, PPO agent | Not started |
| 4. Training | Rewards, train, watch, tune | Not started |
| 5. Shop and higher waves | Smarter shop, wave 10 then 20 | Basic shop rules already in the autopilot |

Already working: with random moves, the autopilot plays full runs back to back and has cleared wave 10.

## How it works

```
Brotato (Godot 3)                          Python
mod_main.gd  ── 73 numbers (JSON line) ──▶ listen.py / brotato_env.py
             ◀── {"action": n, "speed": s} ─
```

1. Every **0.1 s of game time** the mod freezes the game and sends 73 numbers describing the situation.
2. Python picks one of 9 moves and sends it back, together with the game speed.
3. The mod holds that move for the next 0.1 s and the game continues.

This "taking turns" design (lockstep) guarantees every move answers exactly the situation the AI saw, even when Python is slow. If Python does not answer within 3 s, the mod lets go of the keys, returns to normal speed and keeps trying to reconnect.

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
* The mod also sends a small `info` block (enemy count, arena size and so on) for debugging only. The AI does not see it.

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
* The run ends on death or after clearing **wave 10**, then a new run starts with the same setup.

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

1. **After wave 10:** restart the run.
2. **Merge:** if two identical SMGs of the same tier exist, merge them right away into one higher tier SMG. That frees a slot for another SMG.
3. **Buy:**
   * Under **6 weapons**: buy the cheapest SMG it can afford.
   * At **6 weapons**: buy items that give a stat from the priority list above (Max HP, dodge, armor, ranged damage, damage, attack speed, movement speed). Items that lower any of those stats are skipped. The item whose best stat is highest in the list wins.
4. **Money rule (per shop visit):**
   * After buying something, if the money left is less than what was just paid, leave the shop. Prices go up every wave, so it does not waste money on rerolls it cannot follow up on.
   * Only reroll if, after paying for the reroll, there is still enough money for that same price.
   * Before anything is bought in this visit, "that price" is what a basic SMG costs at the current wave (the game's own price formula).
5. Otherwise leave the shop.

### Death and restarts
* "Retry wave" screen: ends the run instead of retrying.
* End of run screen: presses Restart (same character, weapon and danger).

## Planned for the AI (Phase 3 and 4)

Right now Python picks **random** moves, so the character hugs walls and ignores materials. Teaching it to collect materials and avoid corners is the AI's job, through rewards:

| Event | Reward |
| --- | --- |
| Every 0.1 s alive | +0.01 |
| Getting hit | minus 5 × (HP lost / max HP) |
| Picking up a material | +0.05 |
| Clearing a wave | +3 |
| Clearing wave 10 | +10, run ends |
| Dying | minus 5, run ends |
| Touching a wall | minus 0.02 per turn |

These are starting values and will be tuned during training. The AI will be PPO from Stable Baselines3 with a small network (2 layers of 64).

## Files

| File | What it does |
| --- | --- |
| `mod/mod_main.gd` | The Godot mod: observations, lockstep moves, speed, autopilot |
| `make_test_mod.py` | Packs the mod into the zip Steam Brotato loads (Workshop folder) |
| `listen.py` | Test server: prints a summary of what the mod sends, answers with random moves, times each wave |
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
3. `python listen.py 20` (the number is the game speed)
4. Start Brotato and don't touch anything.
5. Stop with Ctrl+C.

Game logs (useful when something breaks): `%APPDATA%\Brotato\logs\godot.log`. Every autopilot action is logged there with a `BrotatoAI autopilot:` prefix.

## Lessons learned

* **Never name Brotato classes in the mod** (`Shop`, `WeaponData`, `TitleScreen` and so on). Godot then loads those scripts while the mod starts, the shop script fails that early, the mod fails, and the game restarts itself with mods disabled ("Recovering from unexpected error in mod... Mods have been temporarily disabled"). The mod recognises screens by their script file path instead.
* **Don't read game objects after a wave ends.** The game deletes its bullet container when a wave ends. Reading it afterwards reads freed memory and can crash the game. The mod stops taking turns as soon as the game starts cleaning up.
* **Raising `Engine.time_scale` alone is not enough** in Godot 3. It makes each physics tick bigger instead of adding ticks, which caps decisions and makes collisions coarse. Raise `Engine.iterations_per_second` with it.
* **Ctrl+C on Windows** does not interrupt a Python socket that waits forever. `listen.py` waits in 0.5 s steps so Ctrl+C works.
