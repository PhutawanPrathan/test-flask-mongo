from flask import Flask, jsonify, request
from pymongo import MongoClient
from datetime import datetime
import threading
import json
import time
import paho.mqtt.client as mqtt
from flask_cors import CORS
from functools import lru_cache
import os

app = Flask(__name__)
CORS(app)

uri = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

# Connection pooling with timeout settings
client = MongoClient(
    uri,
    maxPoolSize=50,
    minPoolSize=10,
    maxIdleTimeMS=45000,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    socketTimeoutMS=10000
)

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

# Create indexes for faster queries
try:
    raw_sensor_collection.create_index([("created_at", -1)])
    inference_collection.create_index([("created_at", -1)])
    collection.create_index([("timestamp", -1)])
    print("✓ Indexes created successfully")
except Exception as e:
    print(f"⚠ Index creation warning: {e}")

latest_data = {
    "mpu1": None,
    "mpu2": None
}

last_sent_time = 0

# Cache for frequently accessed data
cache = {
    "latest_data": None,
    "latest_data_time": 0,
    "status": None,
    "status_time": 0,
    "current_inference": None,
    "current_inference_time": 0,
    "stats": None,
    "stats_time": 0
}

CACHE_DURATION = 1  # Cache for 1 second

def get_cached_or_fetch(cache_key, fetch_function):
    """Generic cache function"""
    current_time = time.time()
    if cache[cache_key] and (current_time - cache[f"{cache_key}_time"]) < CACHE_DURATION:
        return cache[cache_key]
    
    result = fetch_function()
    cache[cache_key] = result
    cache[f"{cache_key}_time"] = current_time
    return result

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
                last_sent_time = current_time
                # Invalidate cache
                cache["latest_data"] = None
                cache["status"] = None

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
# OPTIMIZED SENSOR DATA ENDPOINTS
# ============================================================================

@app.route("/data")
def get_data():
    """Get paginated sensor data - OPTIMIZED with projection and caching"""
    try:
        page = int(request.args.get("page", 1))
        per_page = 10
        skip = (page - 1) * per_page
        
        # Try RPi collection first with projection (only get needed fields)
        projection = {
            "_id": 0,
            "timestamp": 1,
            "created_at": 1,
            "sensor_1.gyro": 1,
            "sensor_1.accel": 1,
            "sensor_2.gyro": 1,
            "sensor_2.accel": 1
        }
        
        rpi_data = list(raw_sensor_collection.find(
            {}, 
            projection
        ).sort("created_at", -1).skip(skip).limit(per_page))
        
        if rpi_data:
            result = []
            for d in rpi_data:
                timestamp = d.get("created_at") or d.get("timestamp")
                timestamp_str = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp) if timestamp else ""
                
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
        
        # Fallback
        old_data = list(collection.find({}, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(per_page))
        result = []
        
        for d in old_data:
            timestamp = d["timestamp"]
            timestamp_str = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
            
            result.append({
                "timestamp": timestamp_str,
                **{k: d.get(k) for k in d if k != "timestamp"}
            })
        
        return jsonify(result)
    except Exception as e:
        print(f"Error in /data: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/latest")
def get_latest():
    """Get latest sensor readings - OPTIMIZED with caching"""
    def fetch_latest():
        # Try RPi collection first
        projection = {
            "_id": 0,
            "timestamp": 1,
            "created_at": 1,
            "sensor_1.gyro": 1,
            "sensor_1.accel": 1,
            "sensor_2.gyro": 1,
            "sensor_2.accel": 1
        }
        
        rpi_data = list(raw_sensor_collection.find({}, projection).sort("created_at", -1).limit(20))
        
        if rpi_data:
            result = []
            for d in reversed(rpi_data):
                timestamp = d.get("created_at") or d.get("timestamp")
                timestamp_str = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp) if timestamp else ""
                
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
            return result
        
        # Fallback
        data = list(collection.find({}, {"_id": 0}).sort("timestamp", -1).limit(20))
        result = []
        
        for d in reversed(data):
            timestamp = d["timestamp"]
            timestamp_str = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
            
            result.append({
                "timestamp": timestamp_str,
                **{k: d.get(k) for k in d if k != "timestamp"}
            })
        
        return result
    
    try:
        result = get_cached_or_fetch("latest_data", fetch_latest)
        return jsonify(result)
    except Exception as e:
        print(f"Error in /latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status")
def get_status():
    """Get sensor status - OPTIMIZED with caching"""
    def fetch_status():
        # Check RPi collection first (only get timestamp field)
        latest_rpi = raw_sensor_collection.find_one({}, {"created_at": 1}, sort=[("created_at", -1)])
        latest_old = collection.find_one({}, {"timestamp": 1}, sort=[("timestamp", -1)])
        
        if latest_rpi:
            timestamp = latest_rpi.get("created_at")
            source = "RPi"
        elif latest_old:
            timestamp = latest_old.get("timestamp")
            source = "MQTT"
        else:
            return {
                "mpu1_online": False,
                "mpu2_online": False,
                "last_update": None,
                "seconds_ago": None,
                "total_records": 0,
                "source": None
            }
        
        if hasattr(timestamp, 'isoformat'):
            last_update_str = timestamp.isoformat()
            time_diff = (datetime.now() - timestamp).total_seconds()
        else:
            last_update_str = str(timestamp)
            time_diff = 999
        
        is_online = time_diff < 30
        
        # Use estimated_document_count for faster counting
        total_records = raw_sensor_collection.estimated_document_count() + collection.estimated_document_count()
        
        return {
            "mpu1_online": is_online,
            "mpu2_online": is_online,
            "last_update": last_update_str,
            "seconds_ago": int(time_diff),
            "total_records": total_records,
            "source": source,
            "rpi_records": raw_sensor_collection.estimated_document_count(),
            "mqtt_records": collection.estimated_document_count()
        }
    
    try:
        result = get_cached_or_fetch("status", fetch_status)
        return jsonify(result)
    except Exception as e:
        print(f"Error in /status: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# OPTIMIZED INFERENCE ENDPOINTS
# ============================================================================

@app.route("/inference/latest")
def get_latest_inference():
    """Get latest inference results - OPTIMIZED"""
    try:
        limit = int(request.args.get("limit", 20))
        
        projection = {
            "_id": 0,
            "timestamp": 1,
            "created_at": 1,
            "current_pattern": 1,
            "next_pattern": 1,
            "inference_time_ms": 1
        }
        
        data = list(inference_collection.find({}, projection).sort("created_at", -1).limit(limit))
        result = []
        
        for d in reversed(data):
            timestamp = d.get("timestamp", "")
            created_at = d.get("created_at")
            created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else ""
            
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
    """Get the most recent inference result - OPTIMIZED with caching"""
    def fetch_current():
        projection = {
            "_id": 0,
            "timestamp": 1,
            "created_at": 1,
            "current_pattern": 1,
            "next_pattern": 1,
            "inference_time_ms": 1
        }
        
        latest = inference_collection.find_one({}, projection, sort=[("created_at", -1)])
        
        if latest:
            timestamp = latest.get("timestamp", "")
            created_at = latest.get("created_at")
            created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else ""
            
            return {
                "timestamp": timestamp,
                "created_at": created_at_str,
                "current_pattern": latest.get("current_pattern", {}),
                "next_pattern": latest.get("next_pattern", {}),
                "inference_time_ms": latest.get("inference_time_ms", 0)
            }
        else:
            return None
    
    try:
        result = get_cached_or_fetch("current_inference", fetch_current)
        if result:
            return jsonify(result)
        else:
            return jsonify({"error": "No inference data available"}), 404
    except Exception as e:
        print(f"Error in /inference/current: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/stats")
def get_inference_stats():
    """Get inference statistics - OPTIMIZED with caching"""
    def fetch_stats():
        total_inferences = inference_collection.estimated_document_count()
        total_raw_samples = raw_sensor_collection.estimated_document_count()
        
        projection = {"_id": 0, "created_at": 1, "inference_time_ms": 1}
        latest = inference_collection.find_one({}, projection, sort=[("created_at", -1)])
        
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
        
        # Calculate average from last 10
        recent_inferences = list(inference_collection.find(
            {}, 
            {"_id": 0, "inference_time_ms": 1}
        ).sort("created_at", -1).limit(10))
        
        if recent_inferences:
            avg_inference_time = sum(d.get("inference_time_ms", 0) for d in recent_inferences) / len(recent_inferences)
        else:
            avg_inference_time = 0
        
        return {
            "total_inferences": total_inferences,
            "total_raw_samples": total_raw_samples,
            "is_active": is_active,
            "last_inference_time": last_inference_time,
            "seconds_since_last": int(time_diff) if time_diff else None,
            "average_inference_time_ms": round(avg_inference_time, 2)
        }
    
    try:
        result = get_cached_or_fetch("stats", fetch_stats)
        return jsonify(result)
    except Exception as e:
        print(f"Error in /inference/stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/history")
def get_inference_history():
    """Get paginated inference history - OPTIMIZED"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        skip = (page - 1) * per_page
        
        projection = {
            "_id": 0,
            "timestamp": 1,
            "created_at": 1,
            "current_pattern": 1,
            "next_pattern": 1,
            "inference_time_ms": 1
        }
        
        data = list(inference_collection.find({}, projection).sort("created_at", -1).skip(skip).limit(per_page))
        total_count = inference_collection.estimated_document_count()
        
        result = []
        for d in data:
            timestamp = d.get("timestamp", "")
            created_at = d.get("created_at")
            created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else ""
            
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


# Keep-alive endpoint for preventing cold starts
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/")
def home():
    return """
    <h1>🤖 Robot Sensor API with Inference (OPTIMIZED)</h1>
    <h2>Sensor Endpoints:</h2>
    <ul>
        <li>GET /data?page=1 - Paginated sensor data (with caching)</li>
        <li>GET /latest - Latest 20 sensor readings (with caching)</li>
        <li>GET /status - Sensor status (with caching)</li>
        <li>GET /ping - Keep-alive endpoint</li>
    </ul>
    <h2>Inference Endpoints:</h2>
    <ul>
        <li>GET /inference/latest?limit=20 - Latest inference results</li>
        <li>GET /inference/current - Most recent inference (with caching)</li>
        <li>GET /inference/stats - Inference statistics (with caching)</li>
        <li>GET /inference/history?page=1&per_page=20 - Paginated history</li>
    </ul>
    <h2>Performance Optimizations:</h2>
    <ul>
        <li>✓ MongoDB connection pooling</li>
        <li>✓ Database indexes on timestamp fields</li>
        <li>✓ Field projection (only fetch needed data)</li>
        <li>✓ Server-side caching (1 second)</li>
        <li>✓ estimated_document_count for faster counts</li>
    </ul>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
