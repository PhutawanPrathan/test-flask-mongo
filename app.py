from flask import Flask, jsonify, request
from pymongo import MongoClient
from datetime import datetime
import threading
import json
import time
import paho.mqtt.client as mqtt
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

uri = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(uri)

# OLD database for MQTT sensor data (kept for backward compatibility)
db = client["sensor_db"]
collection = db["sensor_data"]

# NEW database for inference data (from RPi)
inference_db = client["robot_sensor_data"]
inference_collection = inference_db["inference_results"]
raw_sensor_collection = inference_db["sensor_raw_data"]

print("📦 Connected to MongoDB Atlas")
print(f"  - sensor_db.sensor_data: {collection.count_documents({})} records")
print(f"  - robot_sensor_data.sensor_raw_data: {raw_sensor_collection.count_documents({})} records")
print(f"  - robot_sensor_data.inference_results: {inference_collection.count_documents({})} records")

latest_data = {
    "mpu1": None,
    "mpu2": None
}

last_sent_time = 0

def on_message(client, userdata, msg):
    global last_sent_time
    try:
        payload = json.loads(msg.payload.decode())

        if msg.topic == "esp32/mpu1":
            mpu_id = "mpu1"
        elif msg.topic == "esp32/mpu2":
            mpu_id = "mpu2"
        else:
            return

        latest_data[mpu_id] = payload

        if latest_data["mpu1"] and latest_data["mpu2"]:
            current_time = time.time()
            if current_time - last_sent_time >= 5:
                combined_data = {
                    "timestamp": datetime.now(),
                    "mpu1_ax": latest_data["mpu1"]["accel1X"],
                    "mpu1_ay": latest_data["mpu1"]["accel1Y"],
                    "mpu1_az": latest_data["mpu1"]["accel1Z"],
                    "mpu1_gx": latest_data["mpu1"]["gyro1X"],
                    "mpu1_gy": latest_data["mpu1"]["gyro1Y"],
                    "mpu1_gz": latest_data["mpu1"]["gyro1Z"],
                    "mpu2_ax": latest_data["mpu2"]["accel2X"],
                    "mpu2_ay": latest_data["mpu2"]["accel2Y"],
                    "mpu2_az": latest_data["mpu2"]["accel2Z"],
                    "mpu2_gx": latest_data["mpu2"]["gyro2X"],
                    "mpu2_gy": latest_data["mpu2"]["gyro2Y"],
                    "mpu2_gz": latest_data["mpu2"]["gyro2Z"],
                }
                collection.insert_one(combined_data)
                print("✅ Inserted to sensor_db:", combined_data)
                last_sent_time = current_time

            latest_data["mpu1"] = None
            latest_data["mpu2"] = None

    except Exception as e:
        print("❌ Error:", e)


def mqtt_thread():
    mqtt_client = mqtt.Client()
    mqtt_client.on_message = on_message

    while True:
        try:
            mqtt_client.connect("localhost", 1883, 60)
            mqtt_client.subscribe("esp32/mpu1")
            mqtt_client.subscribe("esp32/mpu2")
            print("🚀 MQTT connected and listening...")
            mqtt_client.loop_forever()
        except Exception as e:
            print("❌ MQTT connection error:", e)
            time.sleep(5)

threading.Thread(target=mqtt_thread, daemon=True).start()


# ============================================================================
# UNIFIED SENSOR DATA ENDPOINTS (reads from BOTH databases)
# ============================================================================

@app.route("/data")
def get_data():
    """Get paginated sensor data - reads from robot_sensor_data (RPi) first, then sensor_db"""
    try:
        page = int(request.args.get("page", 1))
        per_page = 10
        skip = (page - 1) * per_page
        
        # Try to get data from RPi collection first (this is where RPi stores raw data)
        rpi_data = list(raw_sensor_collection.find().sort("created_at", -1).skip(skip).limit(per_page))
        
        if rpi_data:
            # Convert RPi format to frontend format
            result = []
            for d in rpi_data:
                timestamp = d.get("created_at") or d.get("timestamp")
                if hasattr(timestamp, 'isoformat'):
                    timestamp_str = timestamp.isoformat()
                else:
                    timestamp_str = str(timestamp) if timestamp else ""
                
                result.append({
                    "timestamp": timestamp_str,
                    "mpu1_gx": d.get("sensor_1", {}).get("gyro", {}).get("x", 0),
                    "mpu1_gy": d.get("sensor_1", {}).get("gyro", {}).get("y", 0),
                    "mpu1_gz": d.get("sensor_1", {}).get("gyro", {}).get("z", 0),
                    "mpu1_ax": d.get("sensor_1", {}).get("accel", {}).get("x", 0),
                    "mpu1_ay": d.get("sensor_1", {}).get("accel", {}).get("y", 0),
                    "mpu1_az": d.get("sensor_1", {}).get("accel", {}).get("z", 0),
                    "mpu2_gx": d.get("sensor_2", {}).get("gyro", {}).get("x", 0),
                    "mpu2_gy": d.get("sensor_2", {}).get("gyro", {}).get("y", 0),
                    "mpu2_gz": d.get("sensor_2", {}).get("gyro", {}).get("z", 0),
                    "mpu2_ax": d.get("sensor_2", {}).get("accel", {}).get("x", 0),
                    "mpu2_ay": d.get("sensor_2", {}).get("accel", {}).get("y", 0),
                    "mpu2_az": d.get("sensor_2", {}).get("accel", {}).get("z", 0),
                })
            return jsonify(result)
        
        # Fallback: Try old sensor_db collection
        old_data = list(collection.find().sort("timestamp", -1).skip(skip).limit(per_page))
        result = []
        
        for d in old_data:
            timestamp = d["timestamp"]
            if hasattr(timestamp, 'isoformat'):
                timestamp_str = timestamp.isoformat()
            else:
                timestamp_str = str(timestamp)
            
            result.append({
                "timestamp": timestamp_str,
                **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
            })
        
        return jsonify(result)
    except Exception as e:
        print(f"Error in /data: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/latest")
def get_latest():
    """Get latest sensor readings - reads from robot_sensor_data (RPi) first"""
    try:
        # Try RPi collection first
        rpi_data = list(raw_sensor_collection.find().sort("created_at", -1).limit(20))
        
        if rpi_data:
            result = []
            for d in reversed(rpi_data):
                timestamp = d.get("created_at") or d.get("timestamp")
                if hasattr(timestamp, 'isoformat'):
                    timestamp_str = timestamp.isoformat()
                else:
                    timestamp_str = str(timestamp) if timestamp else ""
                
                result.append({
                    "timestamp": timestamp_str,
                    "mpu1_gx": d.get("sensor_1", {}).get("gyro", {}).get("x", 0),
                    "mpu1_gy": d.get("sensor_1", {}).get("gyro", {}).get("y", 0),
                    "mpu1_gz": d.get("sensor_1", {}).get("gyro", {}).get("z", 0),
                    "mpu1_ax": d.get("sensor_1", {}).get("accel", {}).get("x", 0),
                    "mpu1_ay": d.get("sensor_1", {}).get("accel", {}).get("y", 0),
                    "mpu1_az": d.get("sensor_1", {}).get("accel", {}).get("z", 0),
                    "mpu2_gx": d.get("sensor_2", {}).get("gyro", {}).get("x", 0),
                    "mpu2_gy": d.get("sensor_2", {}).get("gyro", {}).get("y", 0),
                    "mpu2_gz": d.get("sensor_2", {}).get("gyro", {}).get("z", 0),
                    "mpu2_ax": d.get("sensor_2", {}).get("accel", {}).get("x", 0),
                    "mpu2_ay": d.get("sensor_2", {}).get("accel", {}).get("y", 0),
                    "mpu2_az": d.get("sensor_2", {}).get("accel", {}).get("z", 0),
                })
            return jsonify(result)
        
        # Fallback: old collection
        data = list(collection.find().sort("timestamp", -1).limit(20))
        result = []
        
        for d in reversed(data):
            timestamp = d["timestamp"]
            if hasattr(timestamp, 'isoformat'):
                timestamp_str = timestamp.isoformat()
            else:
                timestamp_str = str(timestamp)
            
            result.append({
                "timestamp": timestamp_str,
                **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
            })
        
        return jsonify(result)
    except Exception as e:
        print(f"Error in /latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status")
def get_status():
    """Get sensor status - checks both databases"""
    try:
        # Check RPi collection first
        latest_rpi = raw_sensor_collection.find_one(sort=[("created_at", -1)])
        latest_old = collection.find_one(sort=[("timestamp", -1)])
        
        # Use whichever is more recent
        if latest_rpi:
            timestamp = latest_rpi.get("created_at")
            source = "RPi"
        elif latest_old:
            timestamp = latest_old.get("timestamp")
            source = "MQTT"
        else:
            return jsonify({
                "mpu1_online": False,
                "mpu2_online": False,
                "last_update": None,
                "seconds_ago": None,
                "total_records": 0,
                "source": None
            })
        
        if hasattr(timestamp, 'isoformat'):
            last_update_str = timestamp.isoformat()
            time_diff = (datetime.now() - timestamp).total_seconds()
        else:
            last_update_str = str(timestamp)
            time_diff = 999
        
        is_online = time_diff < 30
        
        total_records = raw_sensor_collection.count_documents({}) + collection.count_documents({})
        
        return jsonify({
            "mpu1_online": is_online,
            "mpu2_online": is_online,
            "last_update": last_update_str,
            "seconds_ago": int(time_diff),
            "total_records": total_records,
            "source": source,
            "rpi_records": raw_sensor_collection.count_documents({}),
            "mqtt_records": collection.count_documents({})
        })
    except Exception as e:
        print(f"Error in /status: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# INFERENCE ENDPOINTS
# ============================================================================

@app.route("/inference/latest")
def get_latest_inference():
    """Get latest inference results"""
    try:
        limit = int(request.args.get("limit", 20))
        data = list(inference_collection.find().sort("created_at", -1).limit(limit))
        result = []
        
        for d in reversed(data):
            timestamp = d.get("timestamp", "")
            created_at = d.get("created_at")
            
            if hasattr(created_at, 'isoformat'):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at) if created_at else ""
            
            result.append({
                "timestamp": timestamp,
                "created_at": created_at_str,
                "current_pattern": d.get("current_pattern", {}),
                "next_pattern": d.get("next_pattern", {}),
                "inference_time_ms": d.get("inference_time_ms", 0)
            })
        
        return jsonify(result)
    except Exception as e:
        print(f"Error in /inference/latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/current")
def get_current_inference():
    """Get the most recent inference result"""
    try:
        latest = inference_collection.find_one(sort=[("created_at", -1)])
        
        if latest:
            timestamp = latest.get("timestamp", "")
            created_at = latest.get("created_at")
            
            if hasattr(created_at, 'isoformat'):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at) if created_at else ""
            
            return jsonify({
                "timestamp": timestamp,
                "created_at": created_at_str,
                "current_pattern": latest.get("current_pattern", {}),
                "next_pattern": latest.get("next_pattern", {}),
                "inference_time_ms": latest.get("inference_time_ms", 0)
            })
        else:
            return jsonify({
                "error": "No inference data available"
            }), 404
    except Exception as e:
        print(f"Error in /inference/current: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/stats")
def get_inference_stats():
    """Get inference statistics"""
    try:
        total_inferences = inference_collection.count_documents({})
        total_raw_samples = raw_sensor_collection.count_documents({})
        
        latest = inference_collection.find_one(sort=[("created_at", -1)])
        
        if latest:
            created_at = latest.get("created_at")
            if hasattr(created_at, 'isoformat'):
                time_diff = (datetime.now() - created_at).total_seconds()
                last_inference_time = created_at.isoformat()
            else:
                time_diff = 999
                last_inference_time = str(created_at) if created_at else ""
            
            is_active = time_diff < 60
        else:
            is_active = False
            last_inference_time = None
            time_diff = None
        
        recent_inferences = list(inference_collection.find().sort("created_at", -1).limit(10))
        if recent_inferences:
            avg_inference_time = sum(d.get("inference_time_ms", 0) for d in recent_inferences) / len(recent_inferences)
        else:
            avg_inference_time = 0
        
        return jsonify({
            "total_inferences": total_inferences,
            "total_raw_samples": total_raw_samples,
            "is_active": is_active,
            "last_inference_time": last_inference_time,
            "seconds_since_last": int(time_diff) if time_diff else None,
            "average_inference_time_ms": round(avg_inference_time, 2)
        })
    except Exception as e:
        print(f"Error in /inference/stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/history")
def get_inference_history():
    """Get paginated inference history"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        skip = (page - 1) * per_page
        
        data = list(inference_collection.find().sort("created_at", -1).skip(skip).limit(per_page))
        total_count = inference_collection.count_documents({})
        
        result = []
        for d in data:
            timestamp = d.get("timestamp", "")
            created_at = d.get("created_at")
            
            if hasattr(created_at, 'isoformat'):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at) if created_at else ""
            
            result.append({
                "timestamp": timestamp,
                "created_at": created_at_str,
                "current_pattern": d.get("current_pattern", {}),
                "next_pattern": d.get("next_pattern", {}),
                "inference_time_ms": d.get("inference_time_ms", 0)
            })
        
        return jsonify({
            "data": result,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": (total_count + per_page - 1) // per_page
        })
    except Exception as e:
        print(f"Error in /inference/history: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/")
def home():
    return """
    <h1>🤖 Robot Sensor API with Inference</h1>
    <h2>Sensor Endpoints:</h2>
    <ul>
        <li>GET /data?page=1 - Paginated sensor data (reads from RPi first)</li>
        <li>GET /latest - Latest 20 sensor readings (reads from RPi first)</li>
        <li>GET /status - Sensor status and statistics</li>
    </ul>
    <h2>Inference Endpoints:</h2>
    <ul>
        <li>GET /inference/latest?limit=20 - Latest inference results</li>
        <li>GET /inference/current - Most recent inference</li>
        <li>GET /inference/stats - Inference statistics</li>
        <li>GET /inference/history?page=1&per_page=20 - Paginated history</li>
    </ul>
    <h2>Database Info:</h2>
    <ul>
        <li>RPi raw data: robot_sensor_data.sensor_raw_data</li>
        <li>MQTT data: sensor_db.sensor_data</li>
        <li>Inference results: robot_sensor_data.inference_results</li>
    </ul>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
