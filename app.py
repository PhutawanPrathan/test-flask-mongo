from flask import Flask, jsonify, request
from pymongo import MongoClient, ASCENDING, DESCENDING
from datetime import datetime, timedelta
import threading
import json
import time
import paho.mqtt.client as mqtt
from flask_cors import CORS
from functools import lru_cache
import hashlib

app = Flask(__name__)
CORS(app)

uri = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(
    uri,
    maxPoolSize=50,  # Increase connection pool
    minPoolSize=10,
    maxIdleTimeMS=45000,
    serverSelectionTimeoutMS=5000
)

# Database collections
db = client["sensor_db"]
collection = db["sensor_data"]

inference_db = client["robot_sensor_data"]
inference_collection = inference_db["inference_results"]
raw_sensor_collection = inference_db["sensor_raw_data"]

print("📦 Connected to MongoDB Atlas")

# Create indexes for better query performance
try:
    raw_sensor_collection.create_index([("created_at", DESCENDING)])
    collection.create_index([("timestamp", DESCENDING)])
    inference_collection.create_index([("created_at", DESCENDING)])
    print("✓ Database indexes created")
except Exception as e:
    print(f"Index creation note: {e}")

# ============================================================================
# CACHING LAYER
# ============================================================================

class DataCache:
    """Simple in-memory cache with TTL"""
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
        
    def get(self, key, ttl=2):
        """Get cached value if not expired"""
        if key in self.cache:
            if time.time() - self.timestamps[key] < ttl:
                return self.cache[key]
        return None
    
    def set(self, key, value):
        """Set cached value"""
        self.cache[key] = value
        self.timestamps[key] = time.time()
    
    def clear(self, key=None):
        """Clear specific key or all cache"""
        if key:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)
        else:
            self.cache.clear()
            self.timestamps.clear()

cache = DataCache()

# ============================================================================
# MQTT HANDLER (Keep original functionality)
# ============================================================================

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
                
                # Clear relevant caches
                cache.clear('latest')
                cache.clear('status')

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

def format_sensor_data(d):
    """Helper to format sensor document"""
    timestamp = d.get("created_at") or d.get("timestamp")
    if hasattr(timestamp, 'isoformat'):
        timestamp_str = timestamp.isoformat()
    else:
        timestamp_str = str(timestamp) if timestamp else ""
    
    return {
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
    }

@app.route("/data")
def get_data():
    """Get paginated sensor data with caching - OPTIMIZED"""
    try:
        page = int(request.args.get("page", 1))
        per_page = 10
        
        cache_key = f"data_page_{page}"
        cached = cache.get(cache_key, ttl=3)
        if cached:
            return jsonify(cached)
        
        skip = (page - 1) * per_page
        
        # Query only from RPi collection (most recent data source)
        # Use projection to limit fields returned
        rpi_data = list(raw_sensor_collection.find(
            {},
            {
                '_id': 0,
                'created_at': 1,
                'sensor_1.gyro': 1,
                'sensor_1.accel': 1,
                'sensor_2.gyro': 1,
                'sensor_2.accel': 1
            }
        ).sort("created_at", -1).skip(skip).limit(per_page))
        
        result = [format_sensor_data(d) for d in rpi_data]
        
        cache.set(cache_key, result)
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /data: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/latest")
def get_latest():
    """Get latest sensor readings with aggressive caching - OPTIMIZED"""
    try:
        cached = cache.get('latest', ttl=1)  # 1 second cache
        if cached:
            return jsonify(cached)
        
        # Only get last 20 from RPi, with field projection
        rpi_data = list(raw_sensor_collection.find(
            {},
            {
                '_id': 0,
                'created_at': 1,
                'sensor_1.gyro': 1,
                'sensor_1.accel': 1,
                'sensor_2.gyro': 1,
                'sensor_2.accel': 1
            }
        ).sort("created_at", -1).limit(20))
        
        result = [format_sensor_data(d) for d in reversed(rpi_data)]
        
        cache.set('latest', result)
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status")
def get_status():
    """Get sensor status with caching - OPTIMIZED"""
    try:
        cached = cache.get('status', ttl=2)
        if cached:
            return jsonify(cached)
        
        # Use aggregation pipeline for efficient counting
        latest_rpi = raw_sensor_collection.find_one(
            {},
            {'created_at': 1},
            sort=[("created_at", -1)]
        )
        
        if not latest_rpi:
            result = {
                "mpu1_online": False,
                "mpu2_online": False,
                "last_update": None,
                "seconds_ago": None,
                "total_records": 0,
                "source": None
            }
            cache.set('status', result)
            return jsonify(result)
        
        timestamp = latest_rpi.get("created_at")
        
        if hasattr(timestamp, 'isoformat'):
            last_update_str = timestamp.isoformat()
            time_diff = (datetime.now() - timestamp).total_seconds()
        else:
            last_update_str = str(timestamp)
            time_diff = 999
        
        is_online = time_diff < 30
        
        # Use estimated count for better performance
        total_records = raw_sensor_collection.estimated_document_count()
        
        result = {
            "mpu1_online": is_online,
            "mpu2_online": is_online,
            "last_update": last_update_str,
            "seconds_ago": int(time_diff),
            "total_records": total_records,
            "source": "RPi",
            "rpi_records": total_records,
            "mqtt_records": 0  # Not counting MQTT to save time
        }
        
        cache.set('status', result)
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /status: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# OPTIMIZED INFERENCE ENDPOINTS
# ============================================================================

@app.route("/inference/latest")
def get_latest_inference():
    """Get latest inference results with caching - OPTIMIZED"""
    try:
        limit = int(request.args.get("limit", 20))
        cache_key = f"inference_latest_{limit}"
        
        cached = cache.get(cache_key, ttl=2)
        if cached:
            return jsonify(cached)
        
        data = list(inference_collection.find(
            {},
            {'_id': 0}  # Exclude _id for faster response
        ).sort("created_at", -1).limit(limit))
        
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

        cache.set(cache_key, result)
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /inference/latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/current")
def get_current_inference():
    """Get the most recent inference result with caching - OPTIMIZED"""
    try:
        cached = cache.get('inference_current', ttl=1)
        if cached:
            return jsonify(cached)
        
        latest = inference_collection.find_one(
            {},
            {'_id': 0},
            sort=[("created_at", -1)]
        )

        if latest:
            timestamp = latest.get("timestamp", "")
            created_at = latest.get("created_at")

            if hasattr(created_at, 'isoformat'):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at) if created_at else ""

            result = {
                "timestamp": timestamp,
                "created_at": created_at_str,
                "current_pattern": latest.get("current_pattern", {}),
                "next_pattern": latest.get("next_pattern", {}),
                "inference_time_ms": latest.get("inference_time_ms", 0)
            }
            
            cache.set('inference_current', result)
            return jsonify(result)
        else:
            return jsonify({"error": "No inference data available"}), 404
            
    except Exception as e:
        print(f"Error in /inference/current: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/stats")
def get_inference_stats():
    """Get inference statistics with caching - OPTIMIZED"""
    try:
        cached = cache.get('inference_stats', ttl=5)
        if cached:
            return jsonify(cached)
        
        # Use aggregation for efficient stats calculation
        pipeline = [
            {"$sort": {"created_at": -1}},
            {"$limit": 1},
            {"$project": {
                "created_at": 1,
                "inference_time_ms": 1
            }}
        ]
        
        latest_result = list(inference_collection.aggregate(pipeline))
        
        total_inferences = inference_collection.estimated_document_count()
        total_raw_samples = raw_sensor_collection.estimated_document_count()

        if latest_result:
            latest = latest_result[0]
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

        # Get average from recent inferences only
        recent_pipeline = [
            {"$sort": {"created_at": -1}},
            {"$limit": 10},
            {"$group": {
                "_id": None,
                "avg_time": {"$avg": "$inference_time_ms"}
            }}
        ]
        
        avg_result = list(inference_collection.aggregate(recent_pipeline))
        avg_inference_time = avg_result[0]["avg_time"] if avg_result else 0

        result = {
            "total_inferences": total_inferences,
            "total_raw_samples": total_raw_samples,
            "is_active": is_active,
            "last_inference_time": last_inference_time,
            "seconds_since_last": int(time_diff) if time_diff else None,
            "average_inference_time_ms": round(avg_inference_time, 2)
        }
        
        cache.set('inference_stats', result)
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /inference/stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/inference/history")
def get_inference_history():
    """Get paginated inference history with caching - OPTIMIZED"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        
        cache_key = f"inference_history_{page}_{per_page}"
        cached = cache.get(cache_key, ttl=5)
        if cached:
            return jsonify(cached)
        
        skip = (page - 1) * per_page

        data = list(inference_collection.find(
            {},
            {'_id': 0}
        ).sort("created_at", -1).skip(skip).limit(per_page))
        
        total_count = inference_collection.estimated_document_count()

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

        response = {
            "data": result,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": (total_count + per_page - 1) // per_page
        }
        
        cache.set(cache_key, response)
        return jsonify(response)
        
    except Exception as e:
        print(f"Error in /inference/history: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/")
def home():
    return """
    <h1>🤖 Robot Sensor API with Inference (OPTIMIZED)</h1>
    <h2>Performance Improvements:</h2>
    <ul>
        <li>✓ In-memory caching with TTL</li>
        <li>✓ MongoDB indexes on timestamp fields</li>
        <li>✓ Field projection (only return needed data)</li>
        <li>✓ Estimated counts for faster stats</li>
        <li>✓ Aggregation pipelines for complex queries</li>
        <li>✓ Increased connection pool size</li>
    </ul>
    <h2>Sensor Endpoints:</h2>
    <ul>
        <li>GET /data?page=1 - Paginated sensor data (cached 3s)</li>
        <li>GET /latest - Latest 20 sensor readings (cached 1s)</li>
        <li>GET /status - Sensor status (cached 2s)</li>
    </ul>
    <h2>Inference Endpoints:</h2>
    <ul>
        <li>GET /inference/latest?limit=20 - Latest inference results (cached 2s)</li>
        <li>GET /inference/current - Most recent inference (cached 1s)</li>
        <li>GET /inference/stats - Inference statistics (cached 5s)</li>
        <li>GET /inference/history?page=1 - Paginated history (cached 5s)</li>
    </ul>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
