import datetime
import hashlib
import json
import os
import re
import sqlite3
import struct
import time
import zlib

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)


def init_db():
    conn = sqlite3.connect('leaderboard.db')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lbl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            data INTEGER,
            drivetime INTEGER,
            score_value INTEGER,
            upgrades INTEGER,
            submittime INTEGER,
            checksum TEXT,
            timestamp REAL
        )
    ''')
    conn.commit()
    conn.close()



###########################
### leaderboard section ###
###########################

LEADERBOARDS = {}
GLOBAL_SCORES = {}
PLAYER_STATS_CACHE = {
    "normal": {},
    "donkey": {},
    "crazy": {}
}


def get_category_from_track_id(track_id):
    try:
        tid = int(track_id)
        if tid >= 2**20:
            return "crazy"
        elif tid >= 2**16:
            return "donkey"
        else:
            return "normal"
    except ValueError:
        return "normal"


# TODO query from db
def load_leaderboards():
    folder = "lb/combined_leaderboards"
    for fname in os.listdir(folder):
        if not fname.endswith(".json"):
            continue

        path = os.path.join(folder, fname)

        # Extract numeric track ID
        match = re.search(r'(\d+)', fname)
        if not match:
            continue
        track_id = match.group(1)

        key = f"track{track_id}"

        # Determine category if needed
        category = get_category_from_track_id(track_id)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "results" in data:
                    LEADERBOARDS[key] = data["results"]

                    # Cache player stats per category from first occurrence
                    for entry in data["results"]:
                        pid = entry.get("player")
                        if pid and pid not in PLAYER_STATS_CACHE[category]:
                            stats = entry.get("stats", {})
                            PLAYER_STATS_CACHE[category][pid] = {
                                "submittime": stats.get("submittime", 4096),
                                "data": stats.get("data", 868126464),
                                "upgrades": stats.get("upgrades", 2863270784)
                            }
        except Exception as e:
            print(f"Failed to load {fname}: {e}")


# TODO query from db
def load_global_scores():
    folder = "global"
    categories = {
        "normal": "normal_globalscores.txt",
        "donkey": "donkey_globalscores.txt",
        "crazy": "crazy_globalscores.txt"
    }

    for category, filename in categories.items():
        path = os.path.join(folder, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                scores = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(": ")
                    if len(parts) == 2:
                        player_id = parts[0]
                        score = float(parts[1])
                        scores.append({"player": player_id, "score": score})

                GLOBAL_SCORES[f"global_stats{'_' + category if category != 'normal' else ''}"] = scores
                print(f"Loaded {len(scores)} scores for {category}")
        except Exception as e:
            print(f"Failed to load {filename}: {e}")
            GLOBAL_SCORES[f"global_stats{'_' + category if category != 'normal' else ''}"] = []


# Load on startup
load_leaderboards()
load_global_scores()


# --- track submission ---
@app.route('/public/resultservice/v1/enhancestats/track<int:track_id>', methods=['POST'])
def submit_score(track_id):
    try:
        print("== Incoming POST ==")
        print("   Client IP:", request.remote_addr)
        print("  ", request.get_data())

        json_payload = request.get_json()
        if not json_payload or 'updates' not in json_payload:
            return jsonify({"error": "Invalid JSON format"}), 400

        update = json_payload['updates'][0]
        stats = update.get('stats', {})

        conn = sqlite3.connect('leaderboard.db')
        conn.execute('''
            INSERT INTO lbl (track_id, data, drivetime, score_value, upgrades, submittime, checksum, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            track_id,
            stats.get('data', 0),
            stats.get('drivetime', 0),
            stats.get('score_value', 0),
            stats.get('upgrades', 0),
            stats.get('submittime', 0),
            json_payload.get('checksum', ''),
            time.time()
        ))
        conn.commit()
        conn.close()

        data_val = stats.get('data', 0)
        faults = data_val & 0xFF

        # TODO maybe use session token here instead
        client_ip = request.remote_addr
        last_submission_per_ip[client_ip] = {
            "track_id": track_id,
            "drivetime": stats.get('drivetime', 0),
            "faults": faults,
        }

        print(f"   New score submitted on track {track_id}: drivetime={stats.get('drivetime', 0)}, faults={faults}")
        return jsonify({"updated": 1}), 200

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# --- global score data submission (isnt used for anything) ---
@app.route('/public/playerstats/v1/stats/<category>', methods=['POST'])
def submit_global(category):
    print("== Incoming POST ==")
    print("  ", request.get_data())
    return "Received", 200


# --- populating leaderboard ---
@app.route('/public/playerstats/v1/ranking/<track_id>', methods=['GET'])
def leaderboard(track_id):
    if track_id in ["global_stats", "global_stats_donkey", "global_stats_crazy"]:
        results = GLOBAL_SCORES.get(track_id, [])

        # Determine which category cache to use
        if track_id == "global_stats_donkey":
            category = "donkey"
        elif track_id == "global_stats_crazy":
            category = "crazy"
        else:
            category = "normal"

        # TODO get info from global db instead
        results_with_stats = []
        for entry in results:
            player_id = entry["player"]
            stats = PLAYER_STATS_CACHE[category].get(player_id, {
                "submittime": 4096,
                "data": 868126464,
                "upgrades": 2863270784
            })

            results_with_stats.append({
                "player": player_id,
                "score": entry["score"],
                "stats": {
                    "global_score": entry["score"],
                    "submittime": stats["submittime"],
                    "data": stats["data"],
                    "upgrades": stats["upgrades"]
                }
            })

        results = results_with_stats
    else:
        results = LEADERBOARDS.get(track_id)
        if results is None:
            return jsonify({"error": "track not found"}), 404

    # Get query parameters
    around = request.args.get('around')
    players = request.args.get('players')
    range_ = request.args.get('range')

    sliced_results = results

    if around:
        try:
            player_id, count = around.split(',')
            count = int(count)
            idx = next((i for i, p in enumerate(results) if p["player"] == player_id), 0)
            start = max(idx - count // 2, 0)
            end = min(start + count, len(results))
            sliced_results = results[start:end]
        except Exception:
            sliced_results = results[:]

    elif players:
        player_ids = players.split(',')
        sliced_results = [p for p in results if p["player"] in player_ids]

    elif range_:
        try:
            start_rank, count = map(int, range_.split(','))
            start_idx = start_rank - 1
            end_idx = start_idx + count
            sliced_results = results[start_idx:end_idx]
        except Exception:
            sliced_results = results[:]

    # Add rank to each entry
    for i, entry in enumerate(sliced_results):
        entry["rank"] = results.index(entry) + 1

    data = {
        "results": sliced_results,
        "leaderboard": track_id,
        "playerCount": len(results),
        "httpCode": 200
    }
    return jsonify(data)


#####################
### Ghost section ###
#####################

GHOST_FOLDER = "ghosts"
last_submission_per_ip = {}  # TODO maybe session token instead


def get_hash(s: str) -> int:
    if not s:
        return 0
    v3 = ord(s[0])
    v6 = len(s)
    result = 0
    for c in map(ord, s[1:] + "\x00"):  # include null terminator
        result = (result + v6 * v3) & 0xFFFFFFFF
        v3 = c
        v6 = (18000 * (v6 & 0xFFFF) + (v6 >> 16)) & 0xFFFFFFFF
    return result


def filename_for(profile_id: str, track_name: str) -> str:
    player_hash = get_hash(profile_id)
    track_id = int(track_name[5:])
    return f"ghost_{player_hash:08x}_{track_id}.bin"


# --- not used ---
def extract_drivetime(data: bytes) -> int:
    header, compressed = data[:16], data[16:]
    decompressed = zlib.decompress(compressed)
    return struct.unpack_from("<I", decompressed, 4)[0]



def replay_id_for(filename: str) -> int:
    return abs(hash(filename)) % 1000000


# --- when selecting lb entry ---
@app.route("/public/resultservice/v1/replay_id", methods=["POST"])
def serve_replay_id():
    data = request.get_json()
    print("== Incoming POST ==")
    print("   Received JSON:", data)
    if not data:
        return jsonify({"error": "No JSON"}), 400

    profile_id = data.get("profile_id")
    track_name = data.get("track_name")

    if not profile_id or not track_name or not track_name.startswith("track"):
        return jsonify({"error": "Invalid data"}), 400

    filename = filename_for(profile_id, track_name)
    filepath = os.path.join(GHOST_FOLDER, filename)

    replay_id = replay_id_for(filename)

    if os.path.exists(filepath):
        return jsonify({"replay_id": replay_id}), 200
    return jsonify({"error": "not_found", "replay_id": -1}), 404


# --- after lb result is submitted ---
@app.route("/public/resultservice/v1/replaydata", methods=["GET"])
def get_replaydata():
    track_id = request.args.get("track_id", type=int)
    if track_id is None:
        return jsonify({"error": "missing track_id"}), 400

    # only for test purposes, potentially use id from ubi endpoint from time submission in hash function here? track_id or anything else works anyway
    player_hash = 0x23B85339
    filename = f"ghost_{player_hash:08x}_{track_id}.bin"
    replay_id = replay_id_for(filename)

    return jsonify({"content_id": track_id}), 200


# --- sending ghost file  ---
@app.route("/public/userstorage/v2/content/ghost/<int:replay_id>/payload", methods=["GET", "PUT"])
def handle_ghost_payload(replay_id):
    if request.method == "GET":
        for fname in os.listdir(GHOST_FOLDER):
            if replay_id_for(fname) == replay_id:
                # path = os.path.join(GHOST_FOLDER, "ghost_23b85339_1037.bin")
                path = os.path.join(GHOST_FOLDER, fname)
                return send_file(path, mimetype="application/octet-stream")
        return "Not Found", 404

    if request.method == "PUT":
        client_ip = request.remote_addr
        submission = last_submission_per_ip.get(client_ip)

        # use drivetime and faults from the prior POST
        track_id = submission.get("track_id")
        new_time = submission.get("drivetime", 99999)
        new_faults = submission.get("faults", 250)

        # require track context and matching prior submission
        if track_id is None or not submission or submission.get("track_id") != track_id:
            print(f"No matching submission for IP {client_ip} on track {track_id}")
            return "No matching submission", 400

        data = request.get_data()  # still save the payload if accepted

        # TODO need player_id from main submission or somwhere else to hash here
        os.makedirs(GHOST_FOLDER, exist_ok=True)
        filename = f"ghost_{0x23B85339:08x}_{track_id}.bin"
        filepath = os.path.join(GHOST_FOLDER, filename)

        lb_key = f"track{track_id}"
        old_time = None
        old_faults = None
        if lb_key in LEADERBOARDS:
            for entry in LEADERBOARDS[lb_key]:
                # TODO and here
                if entry.get("player") == "b8b4a39b-08f1-4660-85c5-793d88bf29fc":
                    s = entry.get("stats", {})
                    old_time = s.get("drivetime")
                    old_faults = s.get("data", 0) & 0xFF
                    break

        # # If we have an existing record for the player compare faults first, then time
        # if old_faults is not None:
        #     if new_faults > old_faults:
        #         print(f"    Rejected ghost (faults worse: current={old_faults}, new={new_faults})")
        #         return "IGNORED", 200
        #     if new_faults == old_faults and (old_time is not None and new_time >= old_time):
        #         print(
        #             f"    Rejected slower ghost (same faults={new_faults}, current_time={old_time}, new_time={new_time})")
        #         return "IGNORED", 200

        # accept and save
        with open(filepath, "wb") as f:
            f.write(data)

        print(f"    Saved ghost (faults={new_faults}, time={new_time})")

        last_submission_per_ip.pop(client_ip, None)
        return "OK", 200


# --- ghost add data (not used) ---
@app.route("/public/userstorage/v2/content/ghost", methods=["POST"])
def debug_ghost_post():
    print("=== BODY ===")
    print(request.get_data())
    return "Received", 200


# --- ghost delete and metadata i think (not used) ---
@app.route("/public/userstorage/v2/content/ghost/<int:content_id>", methods=["PUT"])
def debug_ghost_put_root(content_id):
    print(f"PUT root for content_id={content_id}")
    # print("=== HEADERS ===")
    # for k, v in request.headers.items():
    #     print(f"{k}: {v}")
    print("=== BODY ===")
    print(request.get_data())
    return "Received", 200


################################
### Midnight Circuit section ###
################################

@app.route("/public/liveevents/v1/weekly_track_system/basic_info", methods=["GET", "POST"])
def weekly_challenge():

    if request.method == "POST":
        # Print raw POST data
        print("Raw POST data:", request.data)
        print("Form data:", request.form)
        print("JSON data:", request.get_json(silent=True))
        return "POST received", 200
    else:

        # Get current UTC date
        now = datetime.datetime.now(datetime.timezone.utc)

        # Define week boundaries (Monday -> Sunday, example)
        week_start = now - datetime.timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + datetime.timedelta(days=7)

        # Today’s midnight start and end
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)

        response = {
            "week_index": 7,
            "week_start": 0,
            "weeK_end": 0,
            "last_participated_week": 7,
            "last_participated_week_lb": 10000,
            "left_retry_times": 1,
            "midnight_circuit_start_time": 1764547200,
            "midnight_circuit_end_time": 1765152000,
            "last_attempt_time": 1765152000
        }

        return jsonify(response)


@app.route("/public/liveevents/v1/weekly_track_system/config/<int:week_index>", methods=["GET"])
def weekly_configuration(week_index):
    now = datetime.datetime.now(datetime.timezone.utc)

    week_start = now - datetime.timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + datetime.timedelta(days=7)

    response = {
        "week_index": 8,
        "week_settings": {
            "week_start": 0,
            "week_end": 0,
            "week_leaderboard": 10001,
            "global_cooldown_time": 6,
            "global_retry_count": 1,
            "global_gem_amount": 3
        },
        "challenge_settings": {
            "track_id": 1037,
            "bike_id": 5,
            "bike_skin": 3,
            "bike_upgrade": {
                "handle": 10,
                "engine": 10,
                "wheel": 10,
                "frame": 10
            },
            "force_upgrade": 1,
            "task": [
                {
                    "skill": "backflip",
                    "count": 10
                },
                {
                    "skill": "wheelie",
                    "count": 30
                }
            ]
        },
        "retry_pool": [
            {"retry": 1, "gems": "5"},
            {"retry": 2, "gems": "10"},
            {"retry": 3, "gems": "15"}
        ],
        "extra_settings": {
            "ad_reward_skip_cooldown_time": 0,
            "ad_reward_retry_count": 0
        }
    }

    return jsonify(response)


@app.route('/public/liveevents/v1/weekly_track_system/reward_config/<int:week_index>', methods=['GET'])
def get_reward_config(week_index):
    # config = {
    #     "Reward_1": [
    #         {"reward": 101, "amount": 1},
    #         {"reward": 107, "amount": 1},
    #         {"reward": 116, "amount": 1}
    #     ],
    #     "Reward_2": [
    #         {"reward": 201, "amount": 2},
    #         {"reward": 202, "amount": 1}
    #     ],
    #     "Reward_3": [
    #         {"reward": 301, "amount": 3},
    #         {"reward": 302, "amount": 1}
    #     ]
    # }

    # return jsonify(config)
    return "Received", 200


@app.route("/public/liveevents/v1/weekly_track_system/find_opponent", methods=["GET"])
def find_opponent():
    return jsonify({
        "replay_id": 320898,
        "opponent": "b8b4a39b-08f1-4660-85c5-793d88bf29fc"
    })


# TODO function for replying with the lb entry right above the senders id.
# Also used by anba
@app.route("/public/playerstats/v1/stats/<track>", methods=["GET"])
def player_stats(track):
    print("=== QUERY ===", dict(request.args))
    print("=== BODY ===", request.get_data().decode(errors="replace"))

    result = {
        "statboard": track,
        "results": [
            {
                "stats": {
                    "submittime": 0,
                    "drivetime": 15599,
                    "score_value": 359984401,
                    "data": 1158958848,
                    "upgrades": 2863271488,
                    "global_score": 0.90000,
                    "extradata": 0
                },
                "player": "69f4fb35-f9ad-4ae2-a915-47b782322aac",
                "rank": 96,
            }
        ]
    }

    return jsonify(result), 200


@app.route("/public/liveevents/v1/weekly_track_system/start_race", methods=["POST"])
def start_race():
    print("=== BODY ===")
    print(request.data)

    data = request.get_json(force=True)
    return jsonify({
        "gem": data.get("gem"),
        "checksum": data.get("checksum"),
        "timestamp": data.get("timestamp"),
        "restart": data.get("restart")
    }), 200


#####################
### Robot section ###
#####################


@app.route("/public/match/v1/robot", methods=["GET"])
def robot():
    track = request.args.get("track_name")
    result = {
        "player": "b8b4a39b-08f1-4660-85c5-793d88bf29fc",
        "drivetime": 25611,
        "upgrades": 2576944448,
        "data": 1040813824,
        "score_value": 359984389,
        "replay_id": 179164
    }

    return jsonify(result), 200


@app.route("/public/resultservice/v1/robot_replaydata", methods=["GET"])
def handle_robot_payload():
    path = os.path.join(GHOST_FOLDER, "ghost_23b85339_1037.bin")
    return send_file(path, mimetype="application/octet-stream")


##################
### Anti cheat ###
##################


# @app.route("/public/timeservice/v1/gettime", methods=["GET", "POST"])
# def get_time():
#     current_epoch = int(time.time())
#     return jsonify({"time": current_epoch})

if __name__ == '__main__':
    init_db()
    # print(app.url_map)
    app.run(host='0.0.0.0', port=5000, debug=True)
    # app.run(host="0.0.0.0", port=5000, ssl_context=("cert.pem", "key.pem"))
