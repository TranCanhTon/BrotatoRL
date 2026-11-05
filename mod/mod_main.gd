extends Node

const HOST = "127.0.0.1"
const PORT = 9876
const SEND_EVERY = 0.1
const RETRY_EVERY = 1.0

const N_ENEMIES = 8
const N_BULLETS = 8
const N_MATERIALS = 3
const BULLET_SPEED_MAX = 1000.0

var _tcp = StreamPeerTCP.new()
var _send_timer = 0.0
var _retry_timer = 0.0


func _ready():
	print("BrotatoAI: ready, will connect to %s:%d" % [HOST, PORT])


func _process(delta):
	var status = _tcp.get_status()

	if status == StreamPeerTCP.STATUS_NONE or status == StreamPeerTCP.STATUS_ERROR:
		_retry_timer += delta
		if _retry_timer >= RETRY_EVERY:
			_retry_timer = 0.0
			_tcp = StreamPeerTCP.new()
			_tcp.connect_to_host(HOST, PORT)
		return

	if status != StreamPeerTCP.STATUS_CONNECTED:
		return

	_send_timer += delta
	if _send_timer < SEND_EVERY:
		return
	_send_timer = 0.0

	var main = _get_main()
	if main == null:
		return

	var msg = _build_message(main)
	if msg == null:
		return
	_tcp.put_data((to_json(msg) + "\n").to_utf8())


func _get_main():
	var main = get_tree().current_scene
	if main == null or main.get_script() == null:
		return null
	if main.get_script().resource_path != "res://main.gd":
		return null
	if main._players.size() == 0 or not is_instance_valid(main._players[0]):
		return null
	return main


func _build_message(main):
	var p = main._players[0]
	var pos = p.global_position
	var zmin = ZoneService.current_zone_min_position
	var zmax = ZoneService.current_zone_max_position
	var size = zmax - zmin
	if size.x <= 0 or size.y <= 0:
		return null
	var diag = size.length()

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

	# enemies: dx, dy (-1..1), dist (0..1). Empty slot: 0, 0, 1
	var enemies = _closest(main._entity_spawner.get_all_enemies(), pos, N_ENEMIES)
	for i in N_ENEMIES:
		if i < enemies.size():
			var d = enemies[i][1]
			obs.append(clamp(d.x / diag, -1.0, 1.0))
			obs.append(clamp(d.y / diag, -1.0, 1.0))
			obs.append(clamp(d.length() / diag, 0.0, 1.0))
		else:
			obs.append_array([0.0, 0.0, 1.0])

	# bullets: dx, dy, vx, vy (-1..1). Empty slot: zeros
	var bullet_nodes = []
	var max_speed_seen = 0.0
	for child in main._enemy_projectiles.get_children():
		if child is Projectile:
			bullet_nodes.append(child)
			max_speed_seen = max(max_speed_seen, child.velocity.length())
	var bullets = _closest(bullet_nodes, pos, N_BULLETS)
	for i in N_BULLETS:
		if i < bullets.size():
			var d = bullets[i][1]
			var v = bullets[i][2].velocity
			obs.append(clamp(d.x / diag, -1.0, 1.0))
			obs.append(clamp(d.y / diag, -1.0, 1.0))
			obs.append(clamp(v.x / BULLET_SPEED_MAX, -1.0, 1.0))
			obs.append(clamp(v.y / BULLET_SPEED_MAX, -1.0, 1.0))
		else:
			obs.append_array([0.0, 0.0, 0.0, 0.0])

	# materials: dx, dy (-1..1), dist (0..1). Empty slot: 0, 0, 1
	var mats = _closest(main._active_golds, pos, N_MATERIALS)
	for i in N_MATERIALS:
		if i < mats.size():
			var d = mats[i][1]
			obs.append(clamp(d.x / diag, -1.0, 1.0))
			obs.append(clamp(d.y / diag, -1.0, 1.0))
			obs.append(clamp(d.length() / diag, 0.0, 1.0))
		else:
			obs.append_array([0.0, 0.0, 1.0])

	# time left, 0..1
	var wait = max(0.001, main._wave_timer.wait_time)
	obs.append(clamp(main._wave_timer.time_left / wait, 0.0, 1.0))

	var info = {
		"wave": RunData.current_wave,
		"hp": p.current_stats.health,
		"max_hp": p.max_stats.health,
		"arena_w": size.x,
		"arena_h": size.y,
		"diag": diag,
		"n_enemies": main._entity_spawner.get_all_enemies().size(),
		"n_bullets": bullet_nodes.size(),
		"n_materials": main._active_golds.size(),
		"max_bullet_speed": max_speed_seen,
	}
	return {"obs": obs, "info": info}


# returns up to n entries of [distance_squared, offset_from_player, node], closest first
func _closest(nodes, pos, n):
	var arr = []
	for node in nodes:
		if not is_instance_valid(node):
			continue
		var d = node.global_position - pos
		arr.append([d.length_squared(), d, node])
	arr.sort_custom(self, "_by_distance")
	if arr.size() > n:
		arr.resize(n)
	return arr


func _by_distance(a, b):
	return a[0] < b[0]
