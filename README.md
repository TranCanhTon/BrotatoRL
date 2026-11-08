# BrotatoRL

An AI that learns to play the real Steam version of **Brotato** with reinforcement learning, running for free on a normal laptop.

A Godot mod inside the game sends what is happening on screen to Python, Python answers with a move, and the mod plays the rest of the game by itself (menus, level ups, shop, restarts) so training can run overnight with nobody touching anything.

**Current target:** clear wave 20 on Danger 5.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 1. Setup | Python env, unpack game code, test mod, find the game variables | Done |
| 2. Connection | Mod and Python talk, Python controls movement, speed up, autopilot, Gymnasium env | Built. Final 30 minute test left |
| 3. Brain | Rule bot baseline, PPO agent, watch tool | Done: rule bot, `train.py` (10 minute test passed), `watch.py` |
| 4. Training | Long training runs, watch, tune rewards and settings | In progress: brain v2 training from zero |
| 5. Shop and higher waves | Smarter shop | Rule based shop already in the autopilot |

Already working: the autopilot plays full runs back to back (random moves, the rule bot or the AI steering), and the AI trains through it.

Brain v1 (73 numbers) reached an average death wave of 14.1 at Danger 5 after about 500,000 turns, dying mostly on boss waves and sometimes standing still while a boss hit it, because it could not see enemy movement or size. Brain v2 (161 numbers) adds that and more, see below, and trains from zero. The v1 brain is kept in `models/latest.zip`.

## How it works

```
Brotato (Godot 3)                          Python
mod_main.gd  ── 161 numbers (JSON line) ─▶ listen.py / brotato_env.py
             ◀── {"action": n, "speed": s} ─
```

1. Every **0.1 s of game time** the mod freezes the game and sends 161 numbers describing the situation.
2. Python picks one of 9 moves and sends it back, together with the game speed.
3. The mod holds that move for the next 0.1 s and the game continues.

This "taking turns" design (lockstep) guarantees every move answers exactly the situation the AI saw, even when Python is slow. If Python does not answer within 20 s (long enough for the AI to pause and learn during training), the mod lets go of the keys, returns to normal speed and keeps trying to reconnect.

When a wave ends, the mod sends one extra message that Python does not answer:

```
{"event": "wave_end", "wave": 3, "dead": false, "aborted": false, "last_wave": 20}
```

`dead` means the player died, `aborted` means the wave was left without ending (quit to menu, game closed), otherwise the wave was cleared. On the last wave of a run (death or wave 20 cleared) the message also carries a `report`: a log of every autopilot action this run (wave, action, item id, price, item stats) and the character's final stats.

The connection is plain TCP on `127.0.0.1:9876` (your own laptop only), one JSON object per line.

## What the AI sees (161 numbers, layout version 2)

All numbers are scaled to 0..1 or minus 1..1. Empty slots are filled so they mean "nothing there". The layout lives in one place, at the top of `brotato_env.py`, and the mod sends its version (`obs_version`) so a mismatch stops with a clear message.

| Index | What | Count | Scaling |
| --- | --- | --- | --- |
| 0 to 1 | Player position in the arena | 2 | fraction of arena size |
| 2 to 5 | Distance to left, right, top, bottom wall | 4 | fraction of arena size |
| 6 | HP | 1 | HP / max HP |
| 7 to 8 | Own velocity vx, vy | 2 | / 600 |
| 9 to 64 | 8 closest enemies: dx, dy, distance, vx, vy, size, boss | 56 | position / 600, velocity / 800, hitbox radius / 150, boss 1 or 0 (bosses and elites). Empty = 0, 0, 1, 0, 0, 0, 0 |
| 65 to 96 | 8 closest enemy bullets: dx, dy, vx, vy | 32 | position / 600, speed / 600, empty = zeros |
| 97 to 105 | 3 closest materials: dx, dy, distance | 9 | / 600, empty = 0, 0, 1 |
| 106 to 113 | 2 closest pickups: dx, dy, distance, crate | 8 | / 600, crate 1 = item box, 0 = fruit. Empty = 0, 0, 1, 0 |
| 114 to 121 | Enemy radar, 8 directions | 8 | see below |
| 122 to 129 | Bullet radar, 8 directions | 8 | see below |
| 130 | Time left in the wave | 1 | time left / wave length |
| 131 to 144 | Build: max HP, armor, dodge, range, speed, attack speed, HP regen, life steal, damage %, crit chance, weapon count, wave, danger, melee share | 14 | stat / a typical big value (100, 30, 60, 400, 50, 100, 30, 50, 100, 100), weapons / 6, wave / 20, danger / 5, share of melee weapons. Read once per wave |
| 145 to 160 | Spare | 16 | always 0, room to add things later without starting a new brain |

Notes:
* The sight range of 600 keeps close enemies easy to tell apart. Anything further reads as "far" (1).
* **Radar:** the area around the player is split into 8 directions (right, then clockwise: down right, down, down left, left, up left, up, up right). Every enemy closer than 1000 (bullets: 600) adds 1 minus distance / range to its direction, divided by 5 and capped at 1. It counts every enemy and bullet on screen, so the AI knows which way is crowded even past the 8 closest.
* **Why not every enemy:** the brain needs the same number of inputs every turn, and enemy counts go from 0 to 100+. Detail for the closest 8 plus a radar for everything keeps it small.
* Enemy velocity is the enemy's physics velocity, so charges show up. Size is the radius of its hitbox, the same shape the game uses for knockback.
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

Measured on the development laptop (GTX 1650), early waves:

| Asked | Actual, waves 1 to 6 |
| --- | --- |
| 8x | 8x |
| 12x | 11 to 12x |
| 20x | 15 to 16x |

**But use 4x for training.** At 12x the laptop could not keep up in long sessions: after a few minutes it dropped to about 12 turns per second, the whole computer lagged, and the AI played much worse (the same brain died around wave 7 at 12x and around wave 14 at 4x). At 4x it holds about 26 turns per second for hours, about 1 million turns in 11 hours. `train.py` warns above 4. Speed goes back to 1x in menus and when Python disconnects.

## Autopilot

The autopilot plays everything that is not a wave. It only runs while Python is connected, so with Python closed the game plays completely normally. It "clicks" by calling the game's own button functions, so the game behaves exactly as if a person clicked.

All settings live in one block near the top of `mod/mod_main.gd`.

### Run setup
* Character: **Well Rounded**
* Weapon: **SMG**
* Danger: **5**
* The run ends on death or after clearing **wave 20** (the last wave of a normal run), then a new run starts with the same setup.

### Level ups
Picks the first stat offered from this priority list, otherwise the first option:

1. Max HP
2. Dodge
3. Armor
4. Ranged damage
5. Damage %
6. Attack speed
7. Movement speed
8. Crit chance
9. HP regeneration

### Always buy
Bought first whenever it can afford them, ignoring the money rule, and locked when it can't:

| Item | Why |
| --- | --- |
| Tardigrade | Blocks one hit per wave, stacks. No normal stats, so the stat rule would never buy it |

The list is `ALWAYS_BUY` near the top of `mod/mod_main.gd`.

### Banned items
Never bought, and recycled when they come from a crate. Most have a hidden downside the stat check can't see:

| Item | Why |
| --- | --- |
| Weird Ghost | +3 max HP, but you start the next wave with 1 HP |
| Blood Donation | +40 harvesting, but you lose 1 HP every second |
| Alien Baby, Ball and Chain, Bloody Hand, Esty's Couch | Picked by hand |

The list is `BANNED_ITEMS` near the top of `mod/mod_main.gd`.

### Crates (item boxes)
Takes items, except banned ones. Recycles weapons, so weapons only come from the shop rule below.

### Shop
One action every 0.25 s, in this order:

1. **After the last wave:** restart the run.
2. **All 6 slots full and an SMG in the shop with the same tier as one it owns:**
   * if it can afford it: buy it, and the game merges it into the matching SMG by itself
   * if not: lock it and leave the shop right away
3. **All 6 slots full and an SMG in the shop with a higher tier than its weakest SMG:**
   * if gold plus what the weakest SMG sells for covers it: sell the weakest, then buy the better one
   * if not: lock it and leave the shop right away
4. **Buy:**
   * Under **6 weapons**: buy the cheapest SMG it can afford.
   * Otherwise, or if no SMG fits: buy items that give a stat from the priority list above. Items that lower any of those stats, and banned items, are skipped. The item whose best stat is highest in the list wins.
5. **Money rule (per shop visit):**
   * After buying something, if the money left is less than what was just paid, leave the shop. Prices go up every wave, so it does not waste money on rerolls it cannot follow up on.
   * Only reroll if, after paying for the reroll, there is still enough money for that same price.
   * Before anything is bought in this visit, "that price" is what a basic SMG costs at the current wave (the game's own price formula).
6. **Lock before leaving:** anything it would buy but cannot afford gets locked (the game's own lock button), so it is still in the shop next round: SMGs (at 6 weapons only ones that can merge or beat the weakest SMG) and items with a priority stat.
7. Otherwise leave the shop.

What this means for weapons at 6 slots: an SMG is only bought if it can merge (same tier as one owned, below tier 4) or beats the weakest SMG owned. So once all SMGs are tier 4 it stops buying weapons, and lower tier SMGs never get bought or locked.

### Death and restarts
* "Retry wave" screen: ends the run instead of retrying.
* End of run screen: presses Restart (same character, weapon and danger).

## Rule bot (Phase 3 baseline)

`rule_bot.py` is a hand written player that does not learn. It reads the same numbers as the AI (only positions of enemies, bullets and materials) and is scored by the same rewards in `brotato_env.py`. Its score is the bar the trained AI has to beat: if the AI cannot beat it, something is wrong with training.

Every turn (0.1 s of game time) it imagines all 9 moves and picks the one with the best score:

1. **Enemies:** where would I be in 0.3 s, and how close are enemies to that spot? Enemies are assumed to walk toward you, so standing still near a crowd scores badly. Closer enemies count much more. Surrounded, it heads for the biggest gap.
2. **Bullets:** play the next 0.5 s forward. A move that a bullet would pass within 50 units of loses a lot, more if the hit comes sooner. Bullets count 3 times as much as enemies, so it steps sideways out of a bullet's path.
3. **Walls:** a move ending within 100 units of a wall loses points. Corners count both walls.
4. **Materials:** small bonus for getting closer to the nearest material, weaker than the danger, so it only collects when it is safe.
5. **Middle:** a tiny pull toward the center so it does not idle in corners.

All the numbers (speeds, radii, weights) are knobs at the top of the file.

First result, Danger 1, runs ending at wave 12: won 5 of 5 finished runs, average reward about 215. Random moves on the same settings: average wave 3.6, average reward 14.9.

## Rewards

`brotato_env.py` is the referee: it scores every player the same way (random moves, the rule bot and the AI). Only the AI uses the score to learn.

| Event | Reward |
| --- | --- |
| Every 0.1 s alive | +0.01 |
| Getting hit | minus 5 × (HP lost / max HP) |
| Picking up a material (gold went up during a wave) | +0.05 per gold |
| Clearing a wave | +3 |
| Clearing a boss wave (11, 15, 18, 20) | +10 extra |
| Clearing the last wave | +10, run ends |
| Dying | minus 20, run ends |
| Closer than 40 units to a wall | minus 0.02 per turn |

Brain v2 changed dying from minus 5 to minus 20 and added the boss wave bonus. With v1, materials were two thirds of the reward and dying cost less than one wave of materials, so risking a death for materials near a boss paid off.

## The AI (`train.py`)

PPO from Stable Baselines3. The brain is a network, built twice side by side:

* **The player (policy):** 161 numbers in, 2 layers of 256, odds for the 9 moves out. During training it rolls the dice with those odds, so it keeps trying new things.
* **The judge (value):** same input, 2 layers of 256, one number out: how much reward it expects from here on.

How it learns: it plays 2048 turns, then pauses the game (the mod waits, the game freezes for a few seconds). For each turn it compares what really happened afterwards with what the judge expected. Moves that went better than expected become more likely in that kind of situation, worse ones less likely, with each change capped so one lucky run can't wreck it. The judge's guesses get corrected too. Then it plays the next 2048 turns.

Settings at the top of `train.py`: 2048 turns per learning pause, batches of 64, 10 passes per pause, learning rate 0.0003, **gamma 0.995** (the next ~20 s count, v1 used 0.99 = ~10 s), **entropy bonus 0.01** (explores more before settling, v1 used 0), network **[256, 256]** (v1 used [64, 64]). Learning uses at most 2 CPU cores so the rest of the computer stays smooth.

Files, all in `models/v2/` (the v1 brain stays in `models/latest.zip`):

* `latest.zip`: the brain at the end of the last session. Running `train.py` again continues from it.
* `best.zip` and `best.json`: the brain with the best average reward over 20 runs in a row so far. "Latest" is not always "best".
* `brain_info.json`: which layout version the brain was made for. Loading it with a different layout stops with a clear message.
* `checkpoint_..._steps.zip`: backups every 20,000 turns.

After every finished run `train.py` writes:

* one row to `runs_v2.csv` (open it in Excel): wave, reward, turns, materials (total and per wave), damage taken (in full health bars), share of time near a wall, and the reward split into its parts (alive, hit, material, wall, wave, boss, won, died).
* on a death, one line to `deaths_v2.jsonl`: the last 50 turns (5 s) before it, each with the move, HP, closest enemy distance, speed and size, closest boss distance, closest bullet distance and enemy and bullet counts. Shows how it dies instead of guessing.
* TensorBoard graphs (`tb_logs/`, run name `ppo_v2`): the library's own (`rollout/ep_rew_mean` and so on) plus `run/...` and `reward_parts/...`. Open with `tensorboard --logdir tb_logs`, then http://localhost:6006.

`watch.py` loads the saved brain and plays with no learning, at normal speed by default, always taking its best move. Add `dice` to roll the dice like in training, `best` to play `best.zip`.

Brain v1 history: Danger 5, about 500,000 turns, ending at an average death wave of 14.1 (2 hour session, 30 runs: 67% cleared wave 11, 33% cleared wave 15, 3 reached wave 20, 0 wins).

## Files

| File | What it does |
| --- | --- |
| `mod/mod_main.gd` | The Godot mod: observations, lockstep moves, speed, autopilot |
| `make_test_mod.py` | Packs the mod into the zip Steam Brotato loads (Workshop folder) |
| `brotato_env.py` | The adapter between the AI library and the game: Gymnasium `reset()` and `step()`, rewards, one episode per run |
| `rule_bot.py` | Phase 3 baseline: a hand written bot that dodges, collects materials and avoids walls. Does not learn. Same output as `run_random.py`, so the two scores compare directly |
| `train.py` | The learning AI (PPO). Trains for N minutes, saves the brain to `models/v2/`, continues from it next time, keeps the best brain, writes `runs_v2.csv`, `deaths_v2.jsonl`, TensorBoard graphs and `train_log.txt` |
| `watch.py` | Loads the saved brain (latest or best) and plays with no learning, so you can watch what it learned |
| `run_random.py` | Phase 2 final test: random moves play runs through `brotato_env.py` for N minutes, prints one line per run, a run report (everything the autopilot bought, merged, picked on level ups and took from crates, with each item's stats, plus the final character stats) and a summary |
| `listen.py` | Debug tool that stands in for the AI: prints what the mod sends in readable form (a second line shows the v2 numbers: own speed, closest enemy speed, size and boss flag, radar, pickups, build), answers with random moves, times each wave |
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
   * `python run_random.py 30 4` : random runs for 30 minutes at 4x through the Gymnasium env
   * `python rule_bot.py 30 4` : rule bot runs for 30 minutes at 4x (the baseline the AI has to beat)
   * `python train.py 120 4` : train the AI for 2 hours at 4x (continues from the saved brain)
   * `python watch.py` : watch the saved brain play at normal speed (`python watch.py 2` for 2x, `python watch.py 1 dice` to roll the dice like in training, `python watch.py 4 best` for the best brain)
   * `python listen.py 1` : watch what the mod sends, at normal speed
4. Start Brotato and don't touch anything.
5. Stop with Ctrl+C.

Game logs (useful when something breaks): `%APPDATA%\Brotato\logs\godot.log`. Every autopilot action is logged there with a `BrotatoAI autopilot:` prefix.

## Lessons learned

* **Never name Brotato classes in the mod** (`Shop`, `WeaponData`, `TitleScreen` and so on). Godot then loads those scripts while the mod starts, the shop script fails that early, the mod fails, and the game restarts itself with mods disabled ("Recovering from unexpected error in mod... Mods have been temporarily disabled"). The mod recognises screens by their script file path instead.
* **Don't read game objects after a wave ends.** The game deletes its bullet container when a wave ends. Reading it afterwards reads freed memory and can crash the game. The mod stops taking turns as soon as the game starts cleaning up.
* **Some items have hidden downsides** that aren't stats (Weird Ghost sets HP to 1 for the next wave, Blood Donation drains 1 HP per second). The game marks those effects as bad (`effect_sign = 1`) in the item files. For now they go on a hand made ban list.
* **A tier 4 SMG can never merge.** `RunData.get_combine_candidates` still lists a matching tier 4 SMG, so the autopilot kept "buying" one the game refused, about 160,000 times in a row, and training stalled overnight. The mod now asks `RunData.can_combine`, which also checks the max tier, and leaves the shop after 40 actions in one visit no matter what.
* **Windows throttles background programs.** Training ran at about 44 turns per second while the game window was active, and dropped to about 12 as soon as another window was clicked. Fix: `powercfg /powerthrottling disable /path <exe>` (as administrator) for `Brotato.exe` and the venv's `python.exe`, and power mode Best performance.
* **Don't pipe `train.py` into `Tee-Object`.** Ctrl+C then kills Python before it saves the brain. `train.py` writes its own `train_log.txt` instead.
* **Let the wave 1 landing finish first.** Wave 1 starts with the potato dropping in by parachute. Freezing and speeding up the game during that animation sometimes left the potato stuck invisible in the air with its gun, so it died at wave 1 in about a second while enemies chased its health bar. The mod now waits for the landing animation to end before taking turns or speeding up.
* **Raising `Engine.time_scale` alone is not enough** in Godot 3. It makes each physics tick bigger instead of adding ticks, which caps decisions and makes collisions coarse. Raise `Engine.iterations_per_second` with it.
* **High speed can make the AI look worse than it is.** At 12x the laptop fell behind after a few minutes (about 12 turns per second, whole computer lagging) and the same brain died around wave 7 instead of wave 14 at 4x. Train at 4x. Reading files on the laptop through the Claude app during training is also a suspect for lag, so don't.
* **The AI can only react to what it sees.** Brain v1 saw enemy positions but not their movement or size, so a charging boss looked the same as a standing one and a big boss looked farther away than its edge really was. It sometimes stood still while a boss hit it. Brain v2 sends enemy velocity, hitbox size and a boss flag.
* **Ctrl+C on Windows** does not interrupt a Python socket that waits forever. `listen.py` waits in 0.5 s steps so Ctrl+C works.
