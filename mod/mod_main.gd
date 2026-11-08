extends Node

const HOST = "127.0.0.1"
const PORT = 9876
const TURN_EVERY = 0.1        # game seconds between turns
const RETRY_EVERY = 1.0       # real seconds between connection attempts
const REPLY_TIMEOUT_MS = 20000 # give up waiting for Python after this (AI training pauses to learn)

const OBS_VERSION = 2          # layout of the numbers below. Python checks it matches (brotato_env.py)
const N_ENEMIES = 8
const N_BULLETS = 8
const N_MATERIALS = 3
const N_CONSUMABLES = 2        # fruit (heals) and crates
const N_SPARE = 16             # always 0 for now, room to add things later without a new brain
const SIGHT_RANGE = 600.0
const BULLET_SPEED_MAX = 600.0
const PLAYER_SPEED_MAX = 600.0 # player velocity is divided by this
const ENEMY_SPEED_MAX = 800.0  # enemy velocity is divided by this (charges are fast)
const ENEMY_SIZE_MAX = 150.0   # enemy hitbox radius is divided by this (bosses are big)
const RADAR_ENEMY_RANGE = 1000.0  # enemies closer than this count on the radar
const RADAR_BULLET_RANGE = 600.0
const RADAR_FULL = 5.0         # this much "crowd" in one direction = 1.0 on the radar
const SCRIPT_BOSS = "res://entities/units/enemies/bosses/boss.gd"  # bosses and elites
# build stats the AI sees: [game stat id without "stat_", number that counts as 1.0]
const BUILD_STATS = [["max_hp", 100.0], ["armor", 30.0], ["dodge", 60.0], ["range", 400.0],
	["speed", 50.0], ["attack_speed", 100.0], ["hp_regeneration", 30.0], ["lifesteal", 50.0],
	["percent_damage", 100.0], ["crit_chance", 100.0]]
const SPEED_MIN = 0.25
const SPEED_MAX = 20.0

const ACTIONS = ["button_move_left", "button_move_right", "button_move_up", "button_move_down"]
# move number -> [x, y]. 0 stay, 1 up, 2 down, 3 left, 4 right, 5 up left, 6 up right, 7 down left, 8 down right
const MOVES = [[0, 0], [0, -1], [0, 1], [-1, 0], [1, 0], [-1, -1], [1, -1], [-1, 1], [1, 1]]

var _tcp = StreamPeerTCP.new()
var _retry_timer = 0.0
var _turn_timer = 0.0
var _buffer = ""
var _normal_ticks = 60
var _in_wave = false   # true while turns are being taken, to notice when a wave ends
var _wave_number = 0
var _build_obs = []    # build numbers, read once per wave (they don't change during a wave)
var _build_wave = -1   # wave _build_obs was read for

# ---------- autopilot settings ----------
const AUTOPILOT_STEP_MS = 250       # real time between two autopilot clicks
const SCENE_SETTLE_MS = 600         # wait after a new screen opens before clicking
const CHARACTER_ID = "well_rounded"  # part of the game's id for the character
const WEAPON_ID = "smg"              # part of the game's id for the weapon
const DANGER = 5
const MAX_WEAPONS = 6
const MAX_SHOP_ACTIONS = 40          # leave the shop after this many actions in one visit (anti stuck)
const LAST_WAVE = 20                 # the run ends after clearing this wave (20 = last wave of a normal run)
# level up: first one offered from this list wins, top to bottom.
# names are the game's stat ids without "stat_" (see brotato-source/singletons/keys.gd)
const LEVEL_UP_PRIORITY = [
	"max_hp",          # Max HP
	"dodge",           # Dodge
	"armor",           # Armor
	"ranged_damage",   # Ranged Damage
	"percent_damage",  # Damage %
	"attack_speed",    # Attack Speed
	"speed",           # Movement Speed
	"crit_chance",     # Crit Chance
	"hp_regeneration", # HP Regeneration
]
# items the autopilot buys whenever it can afford them, before anything else (and locks when it can't)
const ALWAYS_BUY = [
	"item_tardigrade",    # blocks one hit per wave, stacks
]
# items the autopilot never buys or takes from crates (crates recycle them instead)
const BANNED_ITEMS = [
	"item_weird_ghost",   # +3 max HP, but you start the next wave with 1 HP
	"item_blood_donation", # +40 harvesting, but you lose 1 HP every second
	"item_alien_baby",
	"item_ball_and_chain",
	"item_bloody_hand",
	"item_estys_couch",
]

# which game script runs each screen (files in brotato-source)
const SCRIPT_TITLE = "res://ui/menus/title_screen/title_screen.gd"
const SCRIPT_CHARACTER = "res://ui/menus/run/character_selection.gd"
const SCRIPT_WEAPON = "res://ui/menus/run/weapon_selection.gd"
const SCRIPT_DANGER = "res://ui/menus/run/difficulty_selection/difficulty_selection.gd"
const SCRIPT_SHOP = "res://ui/menus/shop/shop.gd"
const SCRIPT_END_RUN = "res://ui/menus/run/base_end_run.gd"
const SCRIPT_MAIN = "res://main.gd"

var _ap_scene_id = 0
var _ap_scene_since = 0
var _ap_last_step = 0
var _ap_warned = false
var _ap_last_buy_price = 0   # price of the last thing bought in this shop visit (0 = nothing yet)
var _ap_pending_upgrade = ""  # my_id of the better SMG to buy right after selling the weakest one
var _ap_shop_actions = 0      # autopilot actions in this shop visit (safety net against getting stuck)
var _run_log = []            # what the autopilot did this run, sent to Python when the run ends

# character stats reported at the end of a run (names = game stat ids without "stat_")
const REPORT_STATS = ["max_hp", "hp_regeneration", "lifesteal", "percent_damage", "melee_damage",
	"ranged_damage", "elemental_damage", "attack_speed", "crit_chance", "engineering", "range",
	"armor", "dodge", "speed", "luck", "harvesting"]


func _ready():
	_normal_ticks = Engine.iterations_per_second
	print("BrotatoAI: ready, will connect to %s:%d, physics ticks %d" % [HOST, PORT, _normal_ticks])
	for a in ACTIONS:
		if not InputMap.has_action(a):
			print("BrotatoAI: WARNING input action missing: ", a)


func _physics_process(delta):
	var status = _tcp.get_status()

	if status == StreamPeerTCP.STATUS_NONE or status == StreamPeerTCP.STATUS_ERROR:
		_release_all()
		_retry_timer += delta / max(0.001, Engine.time_scale)
		if _retry_timer >= RETRY_EVERY:
			_retry_timer = 0.0
			_tcp = StreamPeerTCP.new()
			_buffer = ""
			_tcp.connect_to_host(HOST, PORT)
		return

	if status != StreamPeerTCP.STATUS_CONNECTED:
		return

	var main = _get_main()
	if main == null:
		if _in_wave:
			_send_wave_end()
		_release_all()
		_set_speed(1.0)
		_turn_timer = 0.0
		return

	# delta in _physics_process is already game time (scaled by Engine.time_scale)
	_turn_timer += delta
	if _turn_timer < TURN_EVERY - 0.0001:
		return
	_turn_timer -= TURN_EVERY

	var msg = _build_message(main)
	if msg == null:
		return
	if _tcp.put_data((to_json(msg) + "\n").to_utf8()) != OK:
		_drop_connection("send failed")
		return

	if not _in_wave:
		if RunData.current_wave == 1:
			_run_log = []   # first turn of a new run
		_log_wave_start(main)
	_in_wave = true
	_wave_number = RunData.current_wave

	var move = _wait_for_move()
	if move < 0:
		return
	_apply_move(move)


# Called once when turns stop. Tells Python how the wave ended:
#   dead    = the player died (the game sets _is_wave_failed before cleaning up)
#   aborted = the wave was left without ending (quit to menu, game closed)
#   otherwise the wave was cleared. Python does not answer this message.
func _send_wave_end():
	_in_wave = false
	_build_wave = -1   # read the build again next wave (shop and level ups change it)
	var scene = get_tree().current_scene
	var dead = false
	var aborted = true
	if scene != null and _runs_script(scene, SCRIPT_MAIN) and scene._cleaning_up:
		aborted = false
		dead = scene._is_wave_failed
	var msg = {
		"event": "wave_end",
		"wave": _wave_number,
		"dead": dead,
		"aborted": aborted,
		"last_wave": LAST_WAVE,
	}
	if not aborted and (dead or _wave_number >= LAST_WAVE):
		msg["report"] = {"log": _run_log, "stats": _character_stats()}
	print("BrotatoAI: wave ", _wave_number, " ended, dead=", dead, " aborted=", aborted)
	if _tcp.put_data((to_json(msg) + "\n").to_utf8()) != OK:
		_drop_connection("send failed")


# Freezes the game until Python answers. Returns the move number, or -1 on failure.
func _wait_for_move():
	var start = OS.get_ticks_msec()
	while true:
		if _tcp.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_drop_connection("Python disconnected")
			return -1
		var n = _tcp.get_available_bytes()
		if n > 0:
			var result = _tcp.get_data(n)
			if result[0] != OK:
				_drop_connection("read failed")
				return -1
			_buffer += result[1].get_string_from_utf8()
			var newline = _buffer.find("\n")
			if newline >= 0:
				var line = _buffer.substr(0, newline)
				_buffer = _buffer.substr(newline + 1)
				var data = parse_json(line)
				if typeof(data) != TYPE_DICTIONARY or not data.has("action"):
					print("BrotatoAI: bad reply: ", line)
					return 0
				if data.has("speed"):
					_set_speed(float(data["speed"]))
				return int(clamp(int(data["action"]), 0, MOVES.size() - 1))
		if OS.get_ticks_msec() - start > REPLY_TIMEOUT_MS:
			_drop_connection("no reply from Python in %d ms" % REPLY_TIMEOUT_MS)
			return -1
		OS.delay_usec(100)
	return -1


func _apply_move(move):
	var m = MOVES[move]
	_set_action("button_move_left", m[0] < 0)
	_set_action("button_move_right", m[0] > 0)
	_set_action("button_move_up", m[1] < 0)
	_set_action("button_move_down", m[1] > 0)


func _set_action(action, pressed):
	if pressed:
		Input.action_press(action)
	else:
		Input.action_release(action)


func _release_all():
	for a in ACTIONS:
		if Input.is_action_pressed(a):
			Input.action_release(a)


# Speeds the game up by running more physics ticks per real second,
# so each tick is still the normal small step (1/60 s of game time).
func _set_speed(speed):
	speed = clamp(speed, SPEED_MIN, SPEED_MAX)
	if Engine.time_scale != speed:
		Engine.time_scale = speed
		Engine.iterations_per_second = int(round(_normal_ticks * max(1.0, speed)))
		print("BrotatoAI: game speed set to ", speed, ", physics ticks ", Engine.iterations_per_second)


func _drop_connection(reason):
	print("BrotatoAI: ", reason)
	_in_wave = false
	_release_all()
	_set_speed(1.0)
	_tcp.disconnect_from_host()
	_buffer = ""
	_retry_timer = 0.0


func _get_main():
	var main = get_tree().current_scene
	if main == null or main.get_script() == null:
		return null
	if main.get_script().resource_path != "res://main.gd":
		return null
	# the wave is over or ending: the game is deleting its pieces, don't touch them
	if main._cleaning_up:
		return null
	if not is_instance_valid(main._enemy_projectiles) or not is_instance_valid(main._entity_spawner) or not is_instance_valid(main._wave_timer):
		return null
	if main._players.size() == 0 or not is_instance_valid(main._players[0]):
		return null
	# wave 1 starts with the potato landing by parachute: don't freeze or speed up
	# the game until that animation is done, or the potato can get stuck invisible
	if _is_landing(main._players[0]):
		return null
	return main


func _is_landing(player):
	var anim = player.get("_animation_player")
	var landing = player.get("animation_landing")
	return anim != null and landing != null and anim.is_playing() and anim.current_animation == landing


func _build_message(main):
	var p = main._players[0]
	var pos = p.global_position
	var zmin = ZoneService.current_zone_min_position
	var zmax = ZoneService.current_zone_max_position
	var size = zmax - zmin
	if size.x <= 0 or size.y <= 0:
		return null

	var obs = []

	# position, 0..1
	obs.append(clamp((pos.x - zmin.x) / size.x, 0.0, 1.0))
	obs.append(clamp((pos.y - zmin.y) / size.y, 0.0, 1.0))

	# wall distances: left, right, top, bottom, 0..1
	obs.append(clamp((pos.x - zmin.x) / size.x, 0.0, 1.0))
	obs.append(clamp((zmax.x - pos.x) / size.x, 0.0, 1.0))
	obs.append(clamp((pos.y - zmin.y) / size.y, 0.0, 1.0))
	obs.append(clamp((zmax.y - pos.y) / size.y, 0.0, 1.0))

	# HP, 0..1
	var max_hp = max(1.0, p.max_stats.health)
	obs.append(clamp(p.current_stats.health / max_hp, 0.0, 1.0))

	# own velocity: vx, vy (-1..1, / PLAYER_SPEED_MAX). The brain has no memory, so this tells it which way it is going
	var pv = p.linear_velocity
	obs.append(clamp(pv.x / PLAYER_SPEED_MAX, -1.0, 1.0))
	obs.append(clamp(pv.y / PLAYER_SPEED_MAX, -1.0, 1.0))

	# enemies, 7 numbers each:
	#   dx, dy (-1..1), dist (0..1), all / SIGHT_RANGE
	#   vx, vy (-1..1, / ENEMY_SPEED_MAX): where it is walking or charging
	#   size (0..1, hitbox radius / ENEMY_SIZE_MAX)
	#   boss (1 = boss or elite, 0 = normal)
	# Empty slot: 0, 0, 1, 0, 0, 0, 0
	var all_enemies = main._entity_spawner.get_all_enemies()
	var enemies = _closest(all_enemies, pos, -1)   # all of them, closest first (radar uses all)
	var max_radius_seen = 0.0
	for i in N_ENEMIES:
		if i < enemies.size():
			var e = enemies[i][2]
			_append_offset(obs, enemies[i][1])
			var ev = e.linear_velocity
			obs.append(clamp(ev.x / ENEMY_SPEED_MAX, -1.0, 1.0))
			obs.append(clamp(ev.y / ENEMY_SPEED_MAX, -1.0, 1.0))
			var radius = _enemy_radius(e)
			max_radius_seen = max(max_radius_seen, radius)
			obs.append(clamp(radius / ENEMY_SIZE_MAX, 0.0, 1.0))
			obs.append(1.0 if _runs_script(e, SCRIPT_BOSS) else 0.0)
		else:
			obs.append_array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])

	# bullets: dx, dy (-1..1, / SIGHT_RANGE), vx, vy (-1..1, / BULLET_SPEED_MAX). Empty slot: zeros
	var bullet_nodes = []
	var max_speed_seen = 0.0
	for child in main._enemy_projectiles.get_children():
		if child is Projectile:
			bullet_nodes.append(child)
			max_speed_seen = max(max_speed_seen, child.velocity.length())
	var bullets = _closest(bullet_nodes, pos, -1)
	for i in N_BULLETS:
		if i < bullets.size():
			var d = bullets[i][1]
			var v = bullets[i][2].velocity
			obs.append(clamp(d.x / SIGHT_RANGE, -1.0, 1.0))
			obs.append(clamp(d.y / SIGHT_RANGE, -1.0, 1.0))
			obs.append(clamp(v.x / BULLET_SPEED_MAX, -1.0, 1.0))
			obs.append(clamp(v.y / BULLET_SPEED_MAX, -1.0, 1.0))
		else:
			obs.append_array([0.0, 0.0, 0.0, 0.0])

	# materials: dx, dy, dist like enemies. Empty slot: 0, 0, 1
	var mats = _closest(main._active_golds, pos, N_MATERIALS)
	for i in N_MATERIALS:
		if i < mats.size():
			_append_offset(obs, mats[i][1])
		else:
			obs.append_array([0.0, 0.0, 1.0])

	# consumables (fruit heals, crates give items): dx, dy, dist, crate (1 = crate, 0 = fruit). Empty slot: 0, 0, 1, 0
	var consumable_nodes = []
	for c in main._consumables:
		if is_instance_valid(c) and not c.already_picked_up:
			consumable_nodes.append(c)
	var cons = _closest(consumable_nodes, pos, N_CONSUMABLES)
	for i in N_CONSUMABLES:
		if i < cons.size():
			_append_offset(obs, cons[i][1])
			obs.append(1.0 if _is_crate(cons[i][2]) else 0.0)
		else:
			obs.append_array([0.0, 0.0, 1.0, 0.0])

	# radar: how crowded each of 8 directions is, counting ALL enemies, then ALL bullets.
	# Directions: 0 right, 1 down right, 2 down, 3 down left, 4 left, 5 up left, 6 up, 7 up right
	obs.append_array(_radar(enemies, RADAR_ENEMY_RANGE))
	obs.append_array(_radar(bullets, RADAR_BULLET_RANGE))

	# time left, 0..1
	var wait = max(0.001, main._wave_timer.wait_time)
	obs.append(clamp(main._wave_timer.time_left / wait, 0.0, 1.0))

	# build: stats, weapon count, wave, danger, melee share (read once per wave)
	if _build_wave != RunData.current_wave:
		_build_obs = _read_build()
		_build_wave = RunData.current_wave
	obs.append_array(_build_obs)

	# spare slots, always 0 for now
	for i in N_SPARE:
		obs.append(0.0)

	var info = {
		"obs_version": OBS_VERSION,
		"n_consumables": consumable_nodes.size(),
		"max_enemy_radius": max_radius_seen,
		"wave": RunData.current_wave,
		"hp": p.current_stats.health,
		"max_hp": p.max_stats.health,
		"arena_w": size.x,
		"arena_h": size.y,
		"sight_range": SIGHT_RANGE,
		"n_enemies": all_enemies.size(),
		"n_bullets": bullet_nodes.size(),
		"n_materials": main._active_golds.size(),
		"max_bullet_speed": max_speed_seen,
		"gold": RunData.get_player_gold(0),
	}
	return {"obs": obs, "info": info}


func _append_offset(obs, d):
	obs.append(clamp(d.x / SIGHT_RANGE, -1.0, 1.0))
	obs.append(clamp(d.y / SIGHT_RANGE, -1.0, 1.0))
	obs.append(clamp(d.length() / SIGHT_RANGE, 0.0, 1.0))


# returns up to n entries of [distance_squared, offset_from_player, node], closest first (n = -1: all)
func _closest(nodes, pos, n):
	var arr = []
	for node in nodes:
		if not is_instance_valid(node):
			continue
		var d = node.global_position - pos
		arr.append([d.length_squared(), d, node])
	arr.sort_custom(self, "_by_distance")
	if n >= 0 and arr.size() > n:
		arr.resize(n)
	return arr


# 8 numbers, one per direction (0 right, then clockwise). Each thing closer than max_range adds
# more the closer it is (1 when touching, 0 at max_range). RADAR_FULL of that = 1.0.
func _radar(entries, max_range):
	var sectors = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
	for entry in entries:
		var dist = sqrt(entry[0])
		if dist >= max_range:
			break   # entries are sorted, everything after is farther
		var d = entry[1]
		var sector = posmod(int(round(atan2(d.y, d.x) / (PI / 4.0))), 8)
		sectors[sector] += 1.0 - dist / max_range
	for i in 8:
		sectors[i] = clamp(sectors[i] / RADAR_FULL, 0.0, 1.0)
	return sectors


# Hitbox radius of an enemy in game units (0 if it can't be read). Same shape the game uses for knockback.
func _enemy_radius(enemy):
	var hurtbox = enemy.get("_hurtbox")
	if hurtbox == null or not is_instance_valid(hurtbox):
		return 0.0
	var collision = hurtbox.get("_collision")
	if collision == null or not is_instance_valid(collision) or collision.shape == null:
		return 0.0
	var shape_scale = abs(collision.global_scale.x)
	var shape = collision.shape
	if "radius" in shape:
		return shape.radius * shape_scale
	if "extents" in shape:
		return max(shape.extents.x, shape.extents.y) * shape_scale
	return 0.0


func _is_crate(consumable):
	var data = consumable.get("consumable_data")
	if data == null:
		return false
	return data.my_id_hash == Keys.consumable_item_box_hash or data.my_id_hash == Keys.consumable_legendary_item_box_hash


# Build numbers (-1..1): BUILD_STATS, weapon count / MAX_WEAPONS, wave / LAST_WAVE, DANGER / 5,
# share of weapons that are melee (0 = all ranged, 1 = all melee)
func _read_build():
	var build = []
	for entry in BUILD_STATS:
		var value = RunData.get_stat(Keys.generate_hash("stat_" + entry[0]), 0)
		build.append(clamp(value / entry[1], -1.0, 1.0))
	var weapons = RunData.get_player_weapons(0)
	build.append(clamp(weapons.size() / float(MAX_WEAPONS), 0.0, 1.0))
	build.append(clamp(RunData.current_wave / float(LAST_WAVE), 0.0, 1.0))
	build.append(clamp(DANGER / 5.0, 0.0, 1.0))
	var melee = 0
	for weapon in weapons:
		if "type" in weapon and int(weapon.type) == 0:   # 0 = melee in the game's WeaponType
			melee += 1
	build.append(melee / float(max(1, weapons.size())))
	return build


func _by_distance(a, b):
	return a[0] < b[0]


# =====================================================================
# AUTOPILOT: clicks through everything that is not a wave.
# Only runs while Python (listen.py) is connected, so normal play is untouched.
# =====================================================================

func _process(_delta):
	if _tcp.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return
	var scene = get_tree().current_scene
	if scene == null:
		return

	# a new screen just opened: remember when, and give it time to finish loading
	var now = OS.get_ticks_msec()
	if scene.get_instance_id() != _ap_scene_id:
		_ap_scene_id = scene.get_instance_id()
		_ap_scene_since = now
		_ap_warned = false
		_ap_last_buy_price = 0
		_ap_pending_upgrade = ""
		_ap_shop_actions = 0
		return
	if now - _ap_scene_since < SCENE_SETTLE_MS or now - _ap_last_step < AUTOPILOT_STEP_MS:
		return
	_ap_last_step = now

	if _runs_script(scene, SCRIPT_TITLE):
		_ap_title_screen()
	elif _runs_script(scene, SCRIPT_CHARACTER):
		_ap_pick_element(scene, "character", funcref(self, "_is_wanted_character"))
	elif _runs_script(scene, SCRIPT_WEAPON):
		_ap_pick_element(scene, "weapon", funcref(self, "_is_wanted_weapon"))
	elif _runs_script(scene, SCRIPT_DANGER):
		_ap_pick_element(scene, "danger", funcref(self, "_is_wanted_danger"))
	elif _runs_script(scene, SCRIPT_SHOP):
		_ap_shop(scene)
	elif _runs_script(scene, SCRIPT_END_RUN):
		_ap_end_run(scene)
	elif _runs_script(scene, SCRIPT_MAIN):
		_ap_between_waves(scene)


# True if the screen runs this script file, or a script built on top of it.
# Comparing file paths avoids loading the game's screen scripts while the mod starts.
func _runs_script(node, path):
	var script = node.get_script()
	while script != null:
		if script.resource_path == path:
			return true
		script = script.get_base_script()
	return false


# ---------- main menu: press Start ----------
func _ap_title_screen():
	print("BrotatoAI autopilot: title screen, starting a new run")
	ProgressData.start_activity()
	var _e = get_tree().change_scene(MenuData.character_selection_scene)


# ---------- character, weapon and danger screens ----------
# Finds the wanted card on the screen and "clicks" it with the game's own function.
func _ap_pick_element(scene, what, is_wanted):
	var seen = []
	for element in scene._get_inventories()[0].get_children():
		if element.is_special or element.is_random or element.item == null:
			continue
		seen.append(str(element.item.my_id))
		if is_wanted.call_func(element.item) and not element.is_locked:
			print("BrotatoAI autopilot: picking ", what, " ", element.item.my_id)
			scene._on_element_pressed(element, 0)
			return
	if not _ap_warned:
		_ap_warned = true
		print("BrotatoAI autopilot: WARNING could not find the ", what, ". On screen: ", seen)


func _is_wanted_character(item):
	return CHARACTER_ID in str(item.my_id)


func _is_wanted_weapon(item):
	return WEAPON_ID in str(item.my_id)


func _is_wanted_danger(item):
	return int(item.value) == DANGER


# ---------- after a wave: level ups, crates, retry screen ----------
func _ap_between_waves(main):
	# death with the "retry wave" option: always end the run instead
	if main._retry_wave.visible:
		print("BrotatoAI autopilot: died, ending the run")
		main._retry_wave._on_CancelButton_pressed()
		return

	if not main._upgrades_ui.visible:
		return
	var box = main._upgrades_ui._get_player_container(0)

	if box._upgrades_container.visible:
		_ap_level_up(box)
	elif box._items_container.visible:
		_ap_crate(box)


# Level up: picks the first stat from LEVEL_UP_PRIORITY that is offered, otherwise the first option.
func _ap_level_up(box):
	var offered = box._old_upgrades
	if offered.empty():
		return
	for wanted in LEVEL_UP_PRIORITY:
		for upgrade in offered:
			if _upgrade_has_stat(upgrade, wanted):
				print("BrotatoAI autopilot: level up, picking ", upgrade.my_id)
				_log_action("level up", upgrade, 0)
				box._on_choose_button_pressed(upgrade)
				return
	print("BrotatoAI autopilot: level up, none wanted, picking first ", offered[0].my_id)
	_log_action("level up", offered[0], 0)
	box._on_choose_button_pressed(offered[0])


# Checks the upgrade's effects for a stat, by name ("stat_max_hp") or by the game's hashed id.
func _upgrade_has_stat(upgrade, stat_name):
	var wanted_hash = Keys.generate_hash("stat_" + stat_name)
	for effect in upgrade.effects:
		var key = effect.get("key")
		if key != null and str(key) == "stat_" + stat_name:
			return true
		var key_hash = effect.get("key_hash")
		if key_hash != null and key_hash == wanted_hash:
			return true
	return false


# Crate (item box): take items, recycle weapons (weapons only come from the shop rule).
func _ap_crate(box):
	var item = box._item_data
	if item == null:
		return
	if _is_weapon(item):
		print("BrotatoAI autopilot: crate weapon, recycling ", item.my_id)
		_log_action("crate recycled", item, 0)
		box._on_DiscardButton_pressed()
		return
	if _is_banned(item):
		print("BrotatoAI autopilot: crate item banned, recycling ", item.my_id)
		_log_action("crate recycled", item, 0)
		box._on_DiscardButton_pressed()
		return
	if item.is_meca:
		box._meca_effect_choice_selected = 0
	print("BrotatoAI autopilot: crate item, taking ", item.my_id)
	_log_action("crate taken", item, 0)
	box._on_TakeButton_pressed()


func _is_banned(item):
	return item != null and str(item.my_id) in BANNED_ITEMS


# Weapons have a "weapon_id" field, items don't. Avoids naming the WeaponData class.
func _is_weapon(item):
	return item != null and "weapon_id" in item


# ---------- shop ----------
# Shop, one action per call (called every 0.25s):
#   1. after LAST_WAVE: restart the run
#   2. at MAX_WEAPONS with a matching SMG in the shop: buy it (the game merges it), else lock it and leave
#   3. at MAX_WEAPONS with a higher tier SMG than our weakest: sell the weakest and buy it
#      (if gold plus the sale covers it), else lock it and leave
#   4. under MAX_WEAPONS: buy an SMG. Otherwise (or if none): buy an item with priority stats
#   4. money rule: after buying, leave if the money left is less than what was just paid.
#      Reroll only if, after paying the reroll, there is still enough for that price.
#      Before buying anything, "that price" is what a basic SMG costs this wave.
func _ap_shop(shop):
	# safety net: if something keeps failing (a buy the game refuses and so on), leave instead of looping forever
	_ap_shop_actions += 1
	if _ap_shop_actions > MAX_SHOP_ACTIONS:
		_ap_leave_shop(shop, "stuck: more than " + str(MAX_SHOP_ACTIONS) + " actions in this visit")
		return

	if RunData.current_wave >= LAST_WAVE:
		_ap_restart_run("cleared wave %d" % RunData.current_wave)
		return

	var weapons = RunData.get_player_weapons(0).size()
	var gold = RunData.get_player_gold(0)

	var items_box = shop._get_shop_items_container(0)

	# just sold the weakest SMG: buy the better one we sold it for
	if _ap_pending_upgrade != "":
		var wanted_id = _ap_pending_upgrade
		_ap_pending_upgrade = ""
		for shop_item in items_box._shop_items:
			if shop_item != null and shop_item.active and str(shop_item.item_data.my_id) == wanted_id and gold >= shop_item.value:
				print("BrotatoAI autopilot: buying ", wanted_id, " for ", shop_item.value, " (upgrade)")
				_log_action("bought (upgrade)", shop_item.item_data, shop_item.value)
				_ap_last_buy_price = shop_item.value
				items_box.on_shop_item_buy_button_pressed(shop_item)
				return

	# always buy items (Tardigrade): first, whenever affordable, no money rule
	for shop_item in items_box._shop_items:
		if shop_item != null and shop_item.active and str(shop_item.item_data.my_id) in ALWAYS_BUY and gold >= shop_item.value:
			print("BrotatoAI autopilot: buying ", shop_item.item_data.my_id, " for ", shop_item.value, " (always buy)")
			_log_action("bought", shop_item.item_data, shop_item.value)
			items_box.on_shop_item_buy_button_pressed(shop_item)
			return

	# All slots full and an SMG in the shop that matches one we own (same tier):
	#   can afford it   -> buy it. The game merges it into the matching SMG by itself
	#                      (its own "buy with full slots" route, no popup involved)
	#   can't afford it -> leave now (leaving locks it for next round)
	if weapons >= MAX_WEAPONS:
		var smg = _ap_find_mergeable_smg(items_box)
		if smg != null:
			if gold >= smg.value:
				print("BrotatoAI autopilot: buying ", smg.item_data.my_id, " for ", smg.value, " (merges into one we own)")
				_log_action("bought + merged", smg.item_data, smg.value)
				_ap_last_buy_price = smg.value
				items_box.on_shop_item_buy_button_pressed(smg)
				return
			_ap_leave_shop(shop, "slots full, SMG costs " + str(smg.value))
			return

		# no merge possible: replace the weakest SMG with a higher tier one from the shop
		var better = _ap_find_better_smg(items_box)
		if better != null:
			var weakest = _weakest_smg()
			var sale = _sell_price(weakest)
			if gold + sale >= better.value:
				print("BrotatoAI autopilot: selling ", weakest.my_id, " for ", sale, " to buy ", better.item_data.my_id, " for ", better.value)
				_log_action("sold", weakest, sale)
				_ap_pending_upgrade = str(better.item_data.my_id)
				shop._on_item_discard_button_pressed(weakest, 0)
				return
			_ap_leave_shop(shop, "slots full, better SMG costs " + str(better.value) + ", have " + str(gold) + " + " + str(sale) + " from selling")
			return

	var target_price = _ap_last_buy_price if _ap_last_buy_price > 0 else _basic_smg_price()

	if _ap_last_buy_price > 0 and gold < _ap_last_buy_price:
		_ap_leave_shop(shop, "can't afford another one at " + str(_ap_last_buy_price))
		return

	var pick = _ap_find_smg(items_box, gold) if weapons < MAX_WEAPONS else null
	if pick == null:
		pick = _ap_find_item(items_box, gold)   # stat items at any weapon count
	if pick != null:
		print("BrotatoAI autopilot: buying ", pick.item_data.my_id, " for ", pick.value)
		_log_action("bought", pick.item_data, pick.value)
		_ap_last_buy_price = pick.value
		items_box.on_shop_item_buy_button_pressed(pick)
		return

	var reroll_price = shop._reroll_price[0]
	if gold - reroll_price >= target_price:
		print("BrotatoAI autopilot: nothing to buy, rerolling for ", reroll_price, " (keeping ", target_price, ")")
		shop._on_RerollButton_pressed(0)
		return

	_ap_leave_shop(shop, "not enough to reroll and still buy")


func _ap_leave_shop(shop, reason):
	_ap_lock_unaffordable(shop)
	print("BrotatoAI autopilot: leaving shop (", reason, ") with ",
		RunData.get_player_weapons(0).size(), " weapons and ", RunData.get_player_gold(0), " gold")
	shop._on_GoButton_pressed(0)


# Before leaving: anything we would buy but can't afford gets locked, so it is
# still in the shop next round (same as clicking its lock button).
#   SMGs (at MAX_WEAPONS only ones that can merge or beat our weakest SMG). Items with a priority stat.
func _ap_lock_unaffordable(shop):
	var at_max = RunData.get_player_weapons(0).size() >= MAX_WEAPONS
	var gold = RunData.get_player_gold(0)
	for shop_item in shop._get_shop_items_container(0)._shop_items:
		if shop_item == null or not shop_item.active or shop_item.locked or gold >= shop_item.value:
			continue
		var item = shop_item.item_data
		var wanted = false
		if _is_weapon(item):
			wanted = WEAPON_ID in str(item.my_id)
			if at_max:
				wanted = wanted and (RunData.can_combine(item, 0) or _is_better_smg(item))
		else:
			wanted = _item_rank(item) < 999 or str(item.my_id) in ALWAYS_BUY
		if wanted:
			print("BrotatoAI autopilot: locking ", item.my_id, " (costs ", shop_item.value, ", have ", gold, ")")
			shop_item.change_lock_status(true)


# At full slots: cheapest SMG in the shop (any price) that matches an SMG we own,
# so buying it makes the game merge the two. null if there is none.
func _ap_find_mergeable_smg(items_box):
	var best = null
	for shop_item in items_box._shop_items:
		if shop_item == null or not shop_item.active:
			continue
		var item = shop_item.item_data
		if not (_is_weapon(item) and WEAPON_ID in str(item.my_id)):
			continue
		# can_combine also checks the max tier: a tier 4 SMG can never merge, even with another tier 4
		if not RunData.can_combine(item, 0):
			continue
		if best == null or shop_item.value < best.value:
			best = shop_item
	return best


# At full slots: highest tier SMG in the shop (any price) that beats our weakest SMG. null if none.
func _ap_find_better_smg(items_box):
	var best = null
	for shop_item in items_box._shop_items:
		if shop_item == null or not shop_item.active:
			continue
		var item = shop_item.item_data
		if _is_weapon(item) and WEAPON_ID in str(item.my_id) and _is_better_smg(item):
			if best == null or item.tier > best.item_data.tier or (item.tier == best.item_data.tier and shop_item.value < best.value):
				best = shop_item
	return best


# True if this SMG has a higher tier than the weakest SMG we own.
func _is_better_smg(item):
	var weakest = _weakest_smg()
	return weakest != null and item.tier > weakest.tier


# The lowest tier SMG we own, or null.
func _weakest_smg():
	var weakest = null
	for weapon in RunData.get_player_weapons(0):
		if WEAPON_ID in str(weapon.my_id) and (weakest == null or weapon.tier < weakest.tier):
			weakest = weapon
	return weakest


# What the game pays for recycling this weapon at this wave (its own formula).
func _sell_price(weapon):
	if weapon == null:
		return 0
	return int(ItemService.get_recycling_value(RunData.current_wave, weapon.value, 0, true))


# Cheapest affordable SMG in the shop, or null.
func _ap_find_smg(items_box, gold):
	var best = null
	for shop_item in items_box._shop_items:
		if shop_item == null or not shop_item.active or gold < shop_item.value:
			continue
		if _is_weapon(shop_item.item_data) and WEAPON_ID in str(shop_item.item_data.my_id):
			if best == null or shop_item.value < best.value:
				best = shop_item
	return best


# Best affordable item: has a stat from LEVEL_UP_PRIORITY, no minus on any of those stats.
# "Best" = its highest stat sits highest in the priority list.
func _ap_find_item(items_box, gold):
	var best = null
	var best_rank = 999
	for shop_item in items_box._shop_items:
		if shop_item == null or not shop_item.active or gold < shop_item.value:
			continue
		if _is_weapon(shop_item.item_data):
			continue
		var rank = _item_rank(shop_item.item_data)
		if rank < best_rank:
			best = shop_item
			best_rank = rank
	return best


# Position in LEVEL_UP_PRIORITY of the item's best stat. 999 = don't buy.
func _item_rank(item):
	if _is_banned(item):
		return 999
	var rank = 999
	for i in LEVEL_UP_PRIORITY.size():
		var value = _stat_value(item, LEVEL_UP_PRIORITY[i])
		if value < 0:
			return 999
		if value > 0 and i < rank:
			rank = i
	return rank


# Total amount an item or upgrade gives of one stat (negative = it lowers it).
func _stat_value(item, stat_name):
	var wanted_hash = Keys.generate_hash("stat_" + stat_name)
	var total = 0
	for effect in item.effects:
		var key = effect.get("key")
		var key_hash = effect.get("key_hash")
		if (key != null and str(key) == "stat_" + stat_name) or (key_hash != null and key_hash == wanted_hash):
			var value = effect.get("value")
			if value != null:
				total += value
	return total


# What the cheapest SMG we own would cost at this wave (the game's own price formula).
# Used as "enough for one SMG" before anything is bought in this shop visit.
func _basic_smg_price():
	var cheapest = null
	for weapon in RunData.get_player_weapons(0):
		if WEAPON_ID in str(weapon.my_id) and (cheapest == null or weapon.value < cheapest.value):
			cheapest = weapon
	if cheapest == null:
		return 0
	return ItemService.get_value(RunData.current_wave, cheapest.value, 0, true, true, cheapest.my_id_hash)


# ---------- run over ----------
func _ap_end_run(end_screen):
	print("BrotatoAI autopilot: run over (", "won" if RunData.run_won else "lost", "), restarting")
	end_screen._on_RestartButton_pressed()


# Same steps as the game's own "Restart" in the pause menu: same character, weapon and danger.
func _ap_restart_run(reason):
	print("BrotatoAI autopilot: ", reason, ", restarting the run")
	ProgressData.reset_and_save_new_run_state()
	RunData.reset(true)
	get_tree().paused = false
	ProgressData.increment_stat("run_started")
	var _e = get_tree().change_scene(MenuData.game_scene)
	ProgressData.start_activity()



# =====================================================================
# RUN REPORT: what the autopilot did, plus final character stats
# =====================================================================

# One entry per action: wave, what happened, item id, price, and the item's stats.
func _log_action(action, item, price):
	_run_log.append({
		"wave": RunData.current_wave,
		"action": action,
		"id": str(item.my_id),
		"price": price,
		"stats": _item_stats(item),
	})


# The stat changes an item, weapon or upgrade gives, e.g. {"max_hp": 3, "speed": -2}.
func _item_stats(item):
	var stats = {}
	var effects = item.get("effects")
	if effects == null:
		return stats
	for effect in effects:
		var key = effect.get("key")
		if key == null or str(key) == "":
			var key_hash = effect.get("key_hash")
			if key_hash != null and Keys.hash_to_string.has(key_hash):
				key = Keys.hash_to_string[key_hash]
		var value = effect.get("value")
		if key == null or value == null or not str(key).begins_with("stat_"):
			continue
		var stat_name = str(key).substr(5)
		stats[stat_name] = stats.get(stat_name, 0) + value
	return stats


# The character's current stats, like the stats panel in the shop.
func _character_stats():
	var stats = {}
	for stat_name in REPORT_STATS:
		stats[stat_name] = RunData.get_stat(Keys.generate_hash("stat_" + stat_name), 0)
	return stats



# First turn of every wave: note which weapons the character really has and its HP.
# (Diagnostic for quick wave 1 deaths after a restart.)
func _log_wave_start(main):
	var weapon_ids = []
	for weapon in RunData.get_player_weapons(0):
		weapon_ids.append(str(weapon.my_id))
	var p = main._players[0]
	var on_player = p.current_weapons.size()
	print("BrotatoAI: wave ", RunData.current_wave, " start, weapons ", weapon_ids,
		" (", on_player, " on the character), hp ", p.current_stats.health, "/", p.max_stats.health,
		", animation ", p._animation_player.current_animation, ", visible ", p.visible)
	_run_log.append({
		"wave": RunData.current_wave,
		"action": "wave start",
		"id": "%d weapons (%d on character): %s" % [weapon_ids.size(), on_player, str(weapon_ids)],
		"price": 0,
		"stats": {"hp": p.current_stats.health, "max_hp": p.max_stats.health},
	})
