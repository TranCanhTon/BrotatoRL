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
var _secondary_obs = []  # secondary stats for the movement brain's spare slots, read with the build
# secondary stats in the movement brain's 16 spare slots: [name, number that counts as 1.0]
# "stat_..." names are read with Utils.get_stat, the others from the effect list
const SECONDARY_STATS = [["stat_luck", 100.0], ["stat_harvesting", 100.0], ["stat_engineering", 30.0],
	["stat_melee_damage", 30.0], ["stat_ranged_damage", 30.0], ["stat_elemental_damage", 30.0],
	["xp_gain", 100.0], ["number_of_enemies", 100.0], ["enemy_health", 100.0], ["enemy_damage", 100.0],
	["enemy_speed", 100.0], ["pickup_range", 100.0], ["knockback", 20.0], ["piercing", 5.0],
	["bounce", 5.0], ["lose_hp_per_second", 5.0]]
# how the current wave is going (for the shop brain): kills, HP lost, gold picked up
var _wave_perf = {}
var _last_wave_perf = {"kills": 0, "hp_lost": 0.0, "gold": 0, "hp_end": 1.0}
var _perf_enemies = {}   # enemy instance ids seen last turn (one that disappears counts as a kill)
var _perf_hp = -1.0
var _perf_gold = -1

# ---------- autopilot settings ----------
const AUTOPILOT_STEP_MS = 250       # real time between two autopilot clicks
const WATCH_STEP_MS = 1200          # same, when watch.py is running: slow enough to follow, with highlights
const SCENE_SETTLE_MS = 600         # wait after a new screen opens before clicking
const CHARACTER_ID = "well_rounded"  # part of the game's id for the character
const WEAPON_ID = "smg"              # part of the game's id for the weapon
const DANGER = 5
const MAX_WEAPONS = 6
const MAX_SHOP_ACTIONS = 40          # leave the shop after this many actions in one visit (anti stuck)
const LAST_WAVE = 20                 # the run ends after clearing this wave (20 = last wave of a normal run)
# Items that stay in the game (137). Everything else is taken out of the shop and crate pool at the
# start of every wave (see _filter_item_pool). Kept: items whose every effect is a stat the shop brain
# can see (now, every wave, next wave, on level up), plus 13 scaling items whose bonus the try on shows.
# Made from items_report.json (scan_items.py). Weapons are never filtered.
const ALLOWED_ITEMS = [
	"item_acid", "item_alien_baby", "item_alien_magic", "item_alien_tongue",
	"item_alien_worm", "item_alloy", "item_bandana", "item_banner",
	"item_bat", "item_bean_teacher", "item_beanie", "item_big_arms",
	"item_black_belt", "item_blindfold", "item_blood_donation", "item_blood_leech",
	"item_bloody_hand", "item_boiling_water", "item_book", "item_bowler_hat",
	"item_boxing_glove", "item_broken_mouth", "item_butterfly", "item_cake",
	"item_cape", "item_celery_tea", "item_charcoal", "item_claw_tree",
	"item_clockwork_wasp", "item_clover", "item_coffee", "item_cog",
	"item_coil", "item_compass", "item_coupon", "item_crown",
	"item_cyclops_worm", "item_dangerous_bunny", "item_decomposing_flesh", "item_defective_steroids",
	"item_diploma", "item_duct_tape", "item_dynamite", "item_energy_bracelet",
	"item_estys_couch", "item_evil_hat", "item_exoskeleton", "item_explosive_shells",
	"item_eyes_surgery", "item_fairy", "item_fertilizer", "item_fin",
	"item_focus", "item_fresh_meat", "item_fuel_tank", "item_gambling_token",
	"item_gentle_alien", "item_glass_cannon", "item_glasses", "item_gnome",
	"item_goat_skull", "item_grinds_magical_leaf", "item_gummy_berserker", "item_head_injury",
	"item_heavy_bullets", "item_hedgehog", "item_helmet", "item_honey",
	"item_injection", "item_insanity", "item_jelly", "item_jetpack",
	"item_leather_vest", "item_lemonade", "item_lens", "item_little_frog",
	"item_little_muscley_dude", "item_lost_duck", "item_lucky_charm", "item_lucky_coin",
	"item_mammoth", "item_mastery", "item_medal", "item_metal_detector",
	"item_metal_plate", "item_missile", "item_mouse", "item_mushroom",
	"item_mutation", "item_night_goggles", "item_octopus", "item_padding",
	"item_panda", "item_peaceful_bee", "item_peacock", "item_pencil",
	"item_plant", "item_plastic_explosive", "item_poisonous_tonic", "item_potato",
	"item_power_generator", "item_propeller_hat", "item_pumpkin", "item_reinforced_steel",
	"item_retromations_hoodie", "item_ricochet", "item_ritual", "item_robot_arm",
	"item_sad_tomato", "item_scar", "item_scope", "item_shady_potion",
	"item_sharp_bullet", "item_shmoop", "item_silver_bullet", "item_small_magazine",
	"item_snail", "item_snake", "item_spider", "item_stone_skin",
	"item_strange_book", "item_sunglasses", "item_terrified_onion", "item_toolbox",
	"item_toxic_sludge", "item_tractor", "item_tree", "item_vigilante_ring",
	"item_warrior_helmet", "item_weird_food", "item_weird_ghost", "item_wheat",
	"item_wheelbarrow", "item_whetstone", "item_white_flag", "item_wings",
	"item_wolf_helmet",
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
var _ap_pending_upgrade = ""  # my_id of the better SMG to buy right after selling the weakest one
var _ap_shop_actions = 0      # autopilot actions in this shop visit (safety net against getting stuck)
var _watch = false           # true while watch.py plays: autopilot goes slower and highlights what it picks
var _ap_highlighted = 0      # instance id of the button highlighted last step (watch mode)

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
		_filter_item_pool()
		_wave_perf = {"kills": 0, "hp_lost": 0.0, "gold": 0, "hp_end": 1.0}
		_perf_enemies = {}
		_perf_hp = -1.0
		_perf_gold = -1
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
	if not _wave_perf.empty():
		_last_wave_perf = _wave_perf
	_wave_perf = {}
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


# Freezes the game until Python answers a turn. Returns the move number, or -1 on failure.
func _wait_for_move():
	var data = _wait_for_reply()
	if data == null:
		return -1
	if not data.has("action"):
		print("BrotatoAI: bad reply (no action): ", data)
		return 0
	_watch = bool(data.get("watch", false))
	if data.has("speed"):
		_set_speed(float(data["speed"]))
	return int(clamp(int(data["action"]), 0, MOVES.size() - 1))


# Freezes the game until Python sends one line. Returns it as a Dictionary, or null on failure
# (the connection is dropped then). Used for turns and for shop brain decisions.
func _wait_for_reply():
	var start = OS.get_ticks_msec()
	while true:
		if _tcp.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_drop_connection("Python disconnected")
			return null
		var n = _tcp.get_available_bytes()
		if n > 0:
			var result = _tcp.get_data(n)
			if result[0] != OK:
				_drop_connection("read failed")
				return null
			_buffer += result[1].get_string_from_utf8()
			var newline = _buffer.find("\n")
			if newline >= 0:
				var line = _buffer.substr(0, newline)
				_buffer = _buffer.substr(newline + 1)
				var data = parse_json(line)
				if typeof(data) != TYPE_DICTIONARY:
					print("BrotatoAI: bad reply: ", line)
					return {}
				return data
		if OS.get_ticks_msec() - start > REPLY_TIMEOUT_MS:
			_drop_connection("no reply from Python in %d ms" % REPLY_TIMEOUT_MS)
			return null
		OS.delay_usec(100)
	return null


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
	_watch = false
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
	if not _wave_perf.empty():
		_track_perf(p, all_enemies)
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
		_secondary_obs = _read_secondary()
		_build_wave = RunData.current_wave
	obs.append_array(_build_obs)

	# spare slots: secondary stats (read once per wave with the build). Were always 0 before,
	# so the movement brain keeps its training
	obs.append_array(_secondary_obs)

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
		# Utils.get_stat = the stats panel total (includes scaling bonuses like Power Generator)
		var value = Utils.get_stat(Keys.generate_hash("stat_" + entry[0]), 0)
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


# 16 secondary stats for the movement brain's spare slots, -1..1
func _read_secondary():
	var effects = RunData.get_player_effects(0)
	var out = []
	for entry in SECONDARY_STATS:
		var h = Keys.generate_hash(entry[0])
		var value = 0.0
		if entry[0].begins_with("stat_"):
			value = Utils.get_stat(h, 0)
		elif effects.has(h) and typeof(effects[h]) in [TYPE_INT, TYPE_REAL]:
			value = effects[h]
		out.append(clamp(value / entry[1], -1.0, 1.0))
	while out.size() < N_SPARE:
		out.append(0.0)
	return out


# Every turn: counts kills (enemies that were there last turn and are gone), HP lost and gold picked up.
func _track_perf(p, all_enemies):
	var now = {}
	for e in all_enemies:
		if is_instance_valid(e):
			now[e.get_instance_id()] = true
	for id in _perf_enemies:
		if not now.has(id):
			_wave_perf["kills"] += 1
	_perf_enemies = now
	var hp = float(p.current_stats.health)
	if _perf_hp >= 0 and hp < _perf_hp:
		_wave_perf["hp_lost"] += (_perf_hp - hp) / max(1.0, float(p.max_stats.health))
	_perf_hp = hp
	var gold = RunData.get_player_gold(0)
	if _perf_gold >= 0 and gold > _perf_gold:
		_wave_perf["gold"] += gold - _perf_gold
	_perf_gold = gold
	_wave_perf["hp_end"] = hp / max(1.0, float(p.max_stats.health))


func _by_distance(a, b):
	return a[0] < b[0]


# =====================================================================
# AUTOPILOT: clicks through everything that is not a wave.
# Only runs while Python is connected, so normal play is untouched.
# Level ups, crates and the shop are decided by the shop brain (Python, shop_env.py).
# Character, weapon, danger, restarts and SMG buying after the brain leaves the shop stay rules.
# =====================================================================

var _ap_choice = -1       # the brain's answer for the current screen, kept until it is carried out (watch mode)
var _ap_smg_phase = false # true after the brain chose "leave": the SMG rules spend what is left, then Go


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
		_ap_pending_upgrade = ""
		_ap_shop_actions = 0
		_ap_choice = -1
		_ap_smg_phase = false
		return
	if now - _ap_scene_since < SCENE_SETTLE_MS or now - _ap_last_step < (WATCH_STEP_MS if _watch else AUTOPILOT_STEP_MS):
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


# Level up: the shop brain picks one of the (up to 4) upgrades.
# After the last wave the run is over for the brain, so it just takes the first one.
func _ap_level_up(box):
	var offered = box._old_upgrades
	if offered.empty():
		return
	if _ap_choice < 0:
		if RunData.current_wave >= LAST_WAVE:
			_ap_choice = 0
		else:
			var mask = _mask_none()
			for i in int(min(offered.size(), N_SLOTS)):
				mask[i] = true
			_ap_choice = _ask_brain("level_up", offered, [], [], mask, null)
			if _ap_choice < 0:
				return
	var pick = offered[int(min(_ap_choice, offered.size() - 1))]
	if _watch_show(_upgrade_button(box, pick)):
		return
	_ap_choice = -1
	print("BrotatoAI shop brain: level up, picking ", pick.my_id)
	_log_action("level up", pick, 0)
	box._on_choose_button_pressed(pick)


# The "Choose" button of the card showing this upgrade (for highlighting in watch mode).
func _upgrade_button(box, upgrade):
	for ui in box._get_upgrade_uis():
		if ui.visible and ui.upgrade_data == upgrade:
			return ui.button
	return null


# Crate (item box): the shop brain decides take (0) or recycle (1).
# Weapons are always recycled (weapons only come from the SMG rules).
func _ap_crate(box):
	var item = box._item_data
	if item == null:
		return
	var recycle = true
	if not _is_weapon(item):
		if _ap_choice < 0:
			if RunData.current_wave >= LAST_WAVE:
				_ap_choice = 0
			else:
				var mask = _mask_none()
				mask[0] = true
				mask[1] = true
				_ap_choice = _ask_brain("crate", [item], [], [], mask, null)
				if _ap_choice < 0:
					return
		recycle = _ap_choice == 1
	if _watch_show(box._discard_button if recycle else box._take_button):
		return
	_ap_choice = -1
	if recycle:
		print("BrotatoAI shop brain: crate, recycling ", item.my_id)
		_log_action("crate recycled", item, 0)
		box._on_DiscardButton_pressed()
		return
	if item.is_meca:
		box._meca_effect_choice_selected = 0
	print("BrotatoAI shop brain: crate, taking ", item.my_id)
	_log_action("crate taken", item, 0)
	box._on_TakeButton_pressed()


# Weapons have a "weapon_id" field, items don't. Avoids naming the WeaponData class.
func _is_weapon(item):
	return item != null and "weapon_id" in item

# ---------- shop ----------
# One action per call (called every 0.25 s, 1.2 s in watch mode):
#   1. after LAST_WAVE: restart the run
#   2. the shop brain picks: buy slot 1-4, lock slot 1-4, reroll or leave
#   3. after "leave" (or MAX_SHOP_ACTIONS): the SMG rules spend what is left, then Go
func _ap_shop(shop):
	_ap_shop_actions += 1
	if RunData.current_wave >= LAST_WAVE:
		_ap_restart_run("cleared wave %d" % RunData.current_wave)
		return

	var items_box = shop._get_shop_items_container(0)
	if not _ap_smg_phase and _ap_shop_actions > MAX_SHOP_ACTIONS:
		print("BrotatoAI shop brain: more than ", MAX_SHOP_ACTIONS, " actions in this shop, leaving")
		_ap_smg_phase = true
	if _ap_smg_phase:
		_ap_smg_rules(shop, items_box)
		return

	var slots = _shop_slots(items_box)
	if _ap_choice < 0:
		var things = []
		var prices = []
		var locked = []
		var mask = _mask_none()
		var gold = RunData.get_player_gold(0)
		var weapons = RunData.get_player_weapons(0).size()
		for i in N_SLOTS:
			var s = slots[i]
			if s == null:
				things.append(null)
				prices.append(0)
				locked.append(false)
				continue
			var item = s.item_data
			things.append(item)
			prices.append(s.value)
			locked.append(s.locked)
			var smg = _is_weapon(item) and WEAPON_ID in str(item.my_id)
			if gold >= s.value:
				if not _is_weapon(item):
					mask[i] = true
				elif smg:
					# a 7th weapon only works if the game can merge it into one we own
					mask[i] = weapons < MAX_WEAPONS or RunData.can_combine(item, 0)
			# lock: anything not locked yet, except weapons that are not SMGs (never bought)
			mask[N_SLOTS + i] = not s.locked and (smg or not _is_weapon(item))
		mask[CHOICE_REROLL] = gold >= shop._reroll_price[0]
		mask[CHOICE_LEAVE] = true
		_ap_choice = _ask_brain("shop", things, prices, locked, mask, shop)
		if _ap_choice < 0:
			return

	var choice = _ap_choice
	var button = null
	if choice < N_SLOTS:
		button = slots[choice]._button if slots[choice] != null else null
	elif choice < 2 * N_SLOTS:
		var lock_slot = slots[choice - N_SLOTS]
		button = lock_slot._lock_button if lock_slot != null else null
	elif choice == CHOICE_REROLL:
		button = shop._get_reroll_button(0)
	if _watch_show(button):
		return
	_ap_choice = -1

	if choice < N_SLOTS:
		var to_buy = slots[choice]
		if to_buy == null or not to_buy.active:
			return
		print("BrotatoAI shop brain: buying ", to_buy.item_data.my_id, " for ", to_buy.value)
		_log_action("bought", to_buy.item_data, to_buy.value)
		items_box.on_shop_item_buy_button_pressed(to_buy)
	elif choice < 2 * N_SLOTS:
		var to_lock = slots[choice - N_SLOTS]
		if to_lock == null or not to_lock.active:
			return
		print("BrotatoAI shop brain: locking ", to_lock.item_data.my_id)
		_log_action("locked", to_lock.item_data, to_lock.value)
		to_lock.change_lock_status(true)
	elif choice == CHOICE_REROLL:
		print("BrotatoAI shop brain: rerolling for ", shop._reroll_price[0])
		shop._on_RerollButton_pressed(0)
	else:
		print("BrotatoAI shop brain: leaving with ", RunData.get_player_gold(0), " gold (SMG rules next)")
		_ap_smg_phase = true


# The first N_SLOTS shop items that can still be bought (null where a slot is empty or sold).
func _shop_slots(items_box):
	var slots = []
	for s in items_box._shop_items:
		if slots.size() >= N_SLOTS:
			break
		slots.append(s if (s != null and s.active) else null)
	while slots.size() < N_SLOTS:
		slots.append(null)
	return slots


# After the brain left: the old SMG rules, one action per call, then Go.
#   under MAX_WEAPONS: buy the cheapest affordable SMG
#   at MAX_WEAPONS: buy an SMG the game can merge into one we own, or sell the weakest SMG for a
#   higher tier one. Nothing left to do: lock SMGs we could not afford and leave.
func _ap_smg_rules(shop, items_box):
	var weapons = RunData.get_player_weapons(0).size()
	var gold = RunData.get_player_gold(0)

	# just sold the weakest SMG: buy the better one we sold it for
	if _ap_pending_upgrade != "":
		var wanted_id = _ap_pending_upgrade
		for shop_item in items_box._shop_items:
			if shop_item != null and shop_item.active and str(shop_item.item_data.my_id) == wanted_id and gold >= shop_item.value:
				if _watch_show(shop_item._button):
					return
				_ap_pending_upgrade = ""
				print("BrotatoAI autopilot: buying ", wanted_id, " for ", shop_item.value, " (upgrade)")
				_log_action("bought (upgrade)", shop_item.item_data, shop_item.value)
				items_box.on_shop_item_buy_button_pressed(shop_item)
				return
		_ap_pending_upgrade = ""

	if weapons >= MAX_WEAPONS:
		var smg = _ap_find_mergeable_smg(items_box)
		if smg != null and gold >= smg.value:
			if _watch_show(smg._button):
				return
			print("BrotatoAI autopilot: buying ", smg.item_data.my_id, " for ", smg.value, " (merges into one we own)")
			_log_action("bought + merged", smg.item_data, smg.value)
			items_box.on_shop_item_buy_button_pressed(smg)
			return
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
	else:
		var pick = _ap_find_smg(items_box, gold)
		if pick != null:
			if _watch_show(pick._button):
				return
			print("BrotatoAI autopilot: buying ", pick.item_data.my_id, " for ", pick.value)
			_log_action("bought", pick.item_data, pick.value)
			items_box.on_shop_item_buy_button_pressed(pick)
			return

	_ap_leave_shop(shop, "SMG rules done")


func _ap_leave_shop(shop, reason):
	if _watch_show(shop._get_go_button(0)):
		return
	_ap_lock_unaffordable_smgs(shop)
	print("BrotatoAI autopilot: leaving shop (", reason, ") with ",
		RunData.get_player_weapons(0).size(), " weapons and ", RunData.get_player_gold(0), " gold")
	shop._on_GoButton_pressed(0)


# Before leaving: SMGs the rules want but can't afford get locked, so they are still there next round.
# (Items are locked by the brain itself.)
func _ap_lock_unaffordable_smgs(shop):
	var at_max = RunData.get_player_weapons(0).size() >= MAX_WEAPONS
	var gold = RunData.get_player_gold(0)
	for shop_item in shop._get_shop_items_container(0)._shop_items:
		if shop_item == null or not shop_item.active or shop_item.locked or gold >= shop_item.value:
			continue
		var item = shop_item.item_data
		if not (_is_weapon(item) and WEAPON_ID in str(item.my_id)):
			continue
		if at_max and not (RunData.can_combine(item, 0) or _is_better_smg(item)):
			continue
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


# =====================================================================
# ITEM POOL: only ALLOWED_ITEMS can show up in the shop and in crates.
# The game draws shop and crate items from copies of ItemService._tiers_data[tier][ITEMS]
# (and ALL_ITEMS). Removing items from those lists removes them from the run. Called at the
# start of every wave; the game rebuilds the lists at the start of a run, so it is repeated.
# =====================================================================
var _allowed = {}

func _filter_item_pool():
	if _allowed.empty():
		for id in ALLOWED_ITEMS:
			_allowed[id] = true
	var removed = 0
	for tier in ItemService._tiers_data:
		for list_index in [0, 1]:   # TierData.ALL_ITEMS, TierData.ITEMS (weapons are kept)
			var kept = []
			for item in tier[list_index]:
				if _is_weapon(item) or _allowed.has(str(item.my_id)):
					kept.append(item)
			removed += tier[list_index].size() - kept.size()
			tier[list_index] = kept
	if removed > 0:
		print("BrotatoAI: took ", removed, " entries out of the shop and crate pool")


# =====================================================================
# SHOP BRAIN: what it sees, and asking Python.
# Message to Python: {"decision": "shop" | "level_up" | "crate", "obs": [SHOP_OBS_SIZE numbers],
#   "mask": [N_CHOICES true/false], "info": {...}}. Python answers {"choice": n}.
# obs layout (version 1), must match shop_env.py:
#   0-2     decision kind: shop, level up, crate
#   3-12    situation: gold, wave, level, reroll price / gold, SMG count, SMG mean tier, SMG best tier,
#           actions this shop visit, items owned, HP now
#   13-16   last wave: kills, HP lost (in max HPs), gold picked up, HP at the end
#   17-60   own stats now (SHOP_STATS, 44)
#   61-102  own "later" pages: every wave, next wave, every level up (LATER_STATS x 3, 42)
#   103-478 4 options x 94: present, SMG, other weapon, price / gold, affordable, locked, tier,
#           copies owned, then what it changes now (try on, 44), then its "later" pages (42)
# Choices: shop 0-3 buy slot, 4-7 lock slot, 8 reroll, 9 leave. Level up 0-3. Crate 0 take, 1 recycle.
# =====================================================================
const SHOP_OBS_VERSION = 1
const N_SLOTS = 4
const N_CHOICES = 10
const CHOICE_REROLL = 8
const CHOICE_LEAVE = 9
const DECISION_KINDS = ["shop", "level_up", "crate"]
const STAT_LIMIT = 3.0
const OPTION_SIZE = 94
# every stat the shop brain sees: [game name, number that counts as 1.0, normal value]
# "stat_..." are read with Utils.get_stat (stats panel total), the rest from the effect list
const SHOP_STATS = [
	["stat_max_hp", 100.0, 0.0], ["stat_hp_regeneration", 20.0, 0.0], ["stat_lifesteal", 30.0, 0.0],
	["stat_percent_damage", 100.0, 0.0], ["stat_melee_damage", 30.0, 0.0], ["stat_ranged_damage", 30.0, 0.0],
	["stat_elemental_damage", 30.0, 0.0], ["stat_attack_speed", 100.0, 0.0], ["stat_crit_chance", 100.0, 0.0],
	["stat_engineering", 30.0, 0.0], ["stat_range", 300.0, 0.0], ["stat_armor", 20.0, 0.0],
	["stat_dodge", 60.0, 0.0], ["stat_speed", 50.0, 0.0], ["stat_luck", 100.0, 0.0],
	["stat_harvesting", 100.0, 0.0], ["consumable_heal", 10.0, 0.0], ["xp_gain", 100.0, 0.0],
	["pickup_range", 100.0, 0.0], ["items_price", 50.0, 0.0], ["explosion_damage", 100.0, 0.0],
	["explosion_size", 100.0, 0.0], ["bounce", 5.0, 0.0], ["piercing", 5.0, 0.0],
	["piercing_damage", 100.0, 0.0], ["damage_against_bosses", 100.0, 0.0], ["structure_attack_speed", 100.0, 0.0],
	["structure_range", 100.0, 0.0], ["burning_cooldown_reduction", 100.0, 0.0], ["burning_spread", 5.0, 0.0],
	["knockback", 20.0, 0.0], ["chance_double_gold", 100.0, 0.0], ["free_rerolls", 5.0, 0.0],
	["trees", 10.0, 0.0], ["number_of_enemies", 100.0, 0.0], ["enemy_speed", 100.0, 0.0],
	["reroll_price", 100.0, 0.0], ["enemy_health", 100.0, 0.0], ["enemy_damage", 100.0, 0.0],
	["lose_hp_per_second", 5.0, 0.0], ["hp_start_next_wave", 100.0, 100.0], ["hp_start_wave", 100.0, 100.0],
	["gold_drops", 100.0, 0.0], ["harvesting_growth", 20.0, 5.0],
]
# stats that can sit on the "later" pages, and the pages (game effect lists)
const LATER_STATS = ["stat_max_hp", "stat_hp_regeneration", "stat_lifesteal", "stat_percent_damage",
	"stat_melee_damage", "stat_ranged_damage", "stat_elemental_damage", "stat_attack_speed",
	"stat_engineering", "stat_armor", "stat_harvesting", "xp_gain", "enemy_health", "enemy_damage"]
const LATER_LISTS = ["stats_end_of_wave", "stats_next_wave", "stats_on_level_up"]
const PLAIN_EFFECT_SCRIPT = "res://items/global/effect.gd"
var _later_by_hash = {}   # stat hash -> index in LATER_STATS


# Builds what the brain sees, sends it, waits for the answer. Returns the choice, or -1 if Python
# did not answer (the connection is dropped then and the autopilot waits for it to come back).
#   things: up to N_SLOTS items / upgrades / weapons (null = empty slot)
#   prices, locked: same order (may be empty: price 0, not locked)
func _ask_brain(kind, things, prices, locked, mask, shop):
	var obs = _context_obs(DECISION_KINDS.find(kind), shop)
	var values = _stat_values()
	for entry in SHOP_STATS:
		obs.append(clamp((values[entry[0]] - entry[2]) / entry[1], -STAT_LIMIT, STAT_LIMIT))
	obs.append_array(_own_later())
	var ids = []
	var price_list = []
	for i in N_SLOTS:
		var thing = things[i] if i < things.size() else null
		var price = prices[i] if i < prices.size() else 0
		var lock = locked[i] if i < locked.size() else false
		obs.append_array(_option_obs(thing, price, lock))
		ids.append(str(thing.my_id) if thing != null else "")
		price_list.append(price)
	# readable stats for decisions.jsonl: only stats that differ from their normal value, short names
	var stats_log = {}
	for entry in SHOP_STATS:
		var v = values[entry[0]]
		if abs(v - entry[2]) > 0.001:
			stats_log[entry[0].replace("stat_", "")] = stepify(v, 0.01)
	stats_log["hp_now"] = RunData.get_player_current_health(0)
	var msg = {
		"decision": kind,
		"obs": obs,
		"mask": mask,
		"info": {"shop_obs_version": SHOP_OBS_VERSION, "wave": RunData.current_wave,
			"gold": RunData.get_player_gold(0), "options": ids, "prices": price_list, "stats": stats_log},
	}
	if _tcp.put_data((to_json(msg) + "\n").to_utf8()) != OK:
		_drop_connection("send failed")
		return -1
	var data = _wait_for_reply()
	if data == null:
		return -1
	var choice = int(data.get("choice", -1))
	if choice < 0 or choice >= N_CHOICES or not mask[choice]:
		print("BrotatoAI shop brain: bad choice ", choice, " for ", kind, ", using the first allowed one")
		choice = mask.find(true)
	return choice


func _mask_none():
	var mask = []
	for i in N_CHOICES:
		mask.append(false)
	return mask


# 17 numbers: decision kind (3), situation (10), last wave (4)
func _context_obs(kind_index, shop):
	var o = [0.0, 0.0, 0.0]
	o[kind_index] = 1.0
	var gold = float(RunData.get_player_gold(0))
	o.append(clamp(gold / 200.0, 0.0, STAT_LIMIT))
	o.append(clamp(RunData.current_wave / float(LAST_WAVE), 0.0, 1.0))
	o.append(clamp(RunData.get_player_level(0) / 30.0, 0.0, 2.0))
	var reroll = 0.0
	if shop != null:
		reroll = clamp(shop._reroll_price[0] / max(1.0, gold), 0.0, STAT_LIMIT)
	o.append(reroll)
	var smg_count = 0
	var tier_sum = 0.0
	var tier_best = 0.0
	for weapon in RunData.get_player_weapons(0):
		if WEAPON_ID in str(weapon.my_id):
			smg_count += 1
			tier_sum += weapon.tier
			tier_best = max(tier_best, weapon.tier)
	o.append(clamp(smg_count / float(MAX_WEAPONS), 0.0, 1.0))
	o.append(clamp(tier_sum / max(1.0, smg_count) / 3.0, 0.0, 1.0))
	o.append(clamp(tier_best / 3.0, 0.0, 1.0))
	o.append(clamp(_ap_shop_actions / float(MAX_SHOP_ACTIONS), 0.0, 1.0))
	o.append(clamp(RunData.get_player_items(0).size() / 50.0, 0.0, 2.0))
	o.append(clamp(RunData.get_player_current_health(0) / float(max(1, RunData.get_player_max_health(0))), 0.0, 1.0))
	o.append(clamp(_last_wave_perf["kills"] / 300.0, 0.0, STAT_LIMIT))
	o.append(clamp(_last_wave_perf["hp_lost"] / 3.0, 0.0, STAT_LIMIT))
	o.append(clamp(_last_wave_perf["gold"] / 300.0, 0.0, STAT_LIMIT))
	o.append(clamp(_last_wave_perf["hp_end"], 0.0, 1.0))
	return o


# 94 numbers for one shop slot, level up card or crate item (all 0 = empty slot)
func _option_obs(thing, price, locked):
	var o = []
	if thing == null:
		for i in OPTION_SIZE:
			o.append(0.0)
		return o
	var gold = RunData.get_player_gold(0)
	var weapon = _is_weapon(thing)
	var smg = weapon and WEAPON_ID in str(thing.my_id)
	o.append(1.0)
	o.append(1.0 if smg else 0.0)
	o.append(1.0 if weapon and not smg else 0.0)
	o.append(clamp(price / max(1.0, float(gold)), 0.0, STAT_LIMIT) if price > 0 else 0.0)
	o.append(1.0 if gold >= price else 0.0)
	o.append(1.0 if locked else 0.0)
	o.append(clamp(float(thing.tier) / 3.0, 0.0, 1.0))
	o.append(clamp(_copies_owned(thing) / 5.0, 0.0, 2.0))
	var changes = {}
	var later = []
	if not weapon and _can_try_on(thing):
		var result = _preview(thing)
		changes = result[0]
		if not result[1].empty():
			print("BrotatoAI shop brain: WARNING ", thing.my_id, " left stats changed after the try on: ", result[1])
		later = _item_later(thing)
	for entry in SHOP_STATS:
		o.append(clamp(changes.get(entry[0], 0.0) / entry[1], -STAT_LIMIT, STAT_LIMIT))
	if later.empty():
		for i in LATER_STATS.size() * LATER_LISTS.size():
			later.append(0.0)
	o.append_array(later)
	return o


# Items from the allowed list and level up upgrades are tried on. Anything else only if all its
# effects are plain stat effects (should not happen, the pool is filtered).
func _can_try_on(thing):
	var id = str(thing.my_id)
	return _allowed.has(id) or id.begins_with("upgrade") or _has_only_plain_effects(thing)


func _copies_owned(thing):
	if _is_weapon(thing):
		var n = 0
		for weapon in RunData.get_player_weapons(0):
			if str(weapon.my_id) == str(thing.my_id):
				n += 1
		return n
	if "my_id_hash" in thing:
		return RunData.get_nb_item(thing.my_id_hash, 0)
	return 0


# {stat name: current value} for every SHOP_STATS entry
func _stat_values():
	var effects = RunData.get_player_effects(0)
	var out = {}
	for entry in SHOP_STATS:
		var name = entry[0]
		var h = Keys.generate_hash(name)
		if name.begins_with("stat_"):
			out[name] = float(Utils.get_stat(h, 0))
		elif effects.has(h) and typeof(effects[h]) in [TYPE_INT, TYPE_REAL]:
			out[name] = float(effects[h])
		else:
			out[name] = 0.0
	return out


func _later_index(key_hash):
	if _later_by_hash.empty():
		for i in LATER_STATS.size():
			_later_by_hash[Keys.generate_hash(LATER_STATS[i])] = i
	return _later_by_hash.get(key_hash, -1)


# 42 numbers: what the character's own "later" pages hold (summed per stat), scaled like SHOP_STATS
func _own_later():
	var effects = RunData.get_player_effects(0)
	var out = []
	for list_name in LATER_LISTS:
		var sums = _later_zeros()
		var h = Keys.generate_hash(list_name)
		if effects.has(h) and typeof(effects[h]) == TYPE_ARRAY:
			for entry in effects[h]:
				if typeof(entry) == TYPE_ARRAY and entry.size() >= 2:
					var i = _later_index(entry[0])
					if i >= 0:
						sums[i] += float(entry[1])
		out.append_array(_later_scaled(sums))
	return out


# 42 numbers: what an item would write on the "later" pages (read from its effects)
func _item_later(thing):
	var out = []
	for list_name in LATER_LISTS:
		var sums = _later_zeros()
		for effect in thing.effects:
			if str(effect.get("custom_key")) == list_name:
				var i = _later_index(Keys.generate_hash(str(effect.get("key"))))
				if i >= 0:
					sums[i] += float(effect.get("value"))
		out.append_array(_later_scaled(sums))
	return out


func _later_zeros():
	var z = []
	for i in LATER_STATS.size():
		z.append(0.0)
	return z


func _later_scaled(sums):
	var out = []
	for i in LATER_STATS.size():
		var scale = 1.0
		for entry in SHOP_STATS:
			if entry[0] == LATER_STATS[i]:
				scale = entry[1]
		out.append(clamp(sums[i] / scale, -STAT_LIMIT, STAT_LIMIT))
	return out


# =====================================================================
# TRY ON: what an item or level up would change on every stat right now.
# Puts its effects on, reads all stats, takes them off again, and checks nothing was left behind.
# Uses the effects directly (not RunData.add_item), so no challenges, item counts or appearances change.
# =====================================================================

# True if every effect is a plain stat effect (effect.gd itself, not a special effect script).
func _has_only_plain_effects(thing):
	for effect in thing.effects:
		var script = effect.get_script()
		if script == null or script.resource_path != PLAIN_EFFECT_SCRIPT:
			return false
	return true


# Returns [changes, leftovers]: changes = {stat: amount} the thing adds right now,
# leftovers = stats that did not go back to their old value (should always be empty).
func _preview(thing):
	var before = _stat_values()
	for effect in thing.effects:
		effect.apply(0)
	_refresh_stats()
	var after = _stat_values()
	var reversed = thing.effects.duplicate()
	reversed.invert()
	for effect in reversed:
		effect.unapply(0)
	_refresh_stats()
	var back = _stat_values()
	var changes = {}
	var leftovers = {}
	for name in before:
		if abs(after[name] - before[name]) > 0.001:
			changes[name] = after[name] - before[name]
		if abs(back[name] - before[name]) > 0.001:
			leftovers[name] = back[name] - before[name]
	return [changes, leftovers]


# Makes the game recalculate everything that depends on other stats or on owned items/weapons.
func _refresh_stats():
	RunData.update_item_related_effects(0)
	LinkedStats.reset_player(0)
	Utils.reset_stat_cache(0)

# Watch mode only: highlights the button the autopilot is about to press (the game's own focus
# highlight) and returns true, so the press happens on the next autopilot step. Returns false when
# the button was already highlighted last step (press it now), or outside watch mode.
func _watch_show(button):
	if not _watch or button == null or not is_instance_valid(button) or not button.is_visible_in_tree():
		return false
	if _ap_highlighted == button.get_instance_id():
		_ap_highlighted = 0
		return false
	button.focus_mode = Control.FOCUS_ALL
	button.grab_focus()
	_ap_highlighted = button.get_instance_id()
	_ap_shop_actions = max(0, _ap_shop_actions - 1)   # highlighting is not a shop action
	return true
