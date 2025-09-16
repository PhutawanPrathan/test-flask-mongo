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
db = client["sensor_db"]
collection = db["sensor_data"]

collection.create_index("timestamp", expireAfterSeconds=100)
print("📦 Index info:", list(collection.index_information()))

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
                print("✅ Inserted:", combined_data)
                last_sent_time = current_time

            # รีเซ็ตให้รอข้อมูลรอบใหม่
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

@app.route("/data")
def get_data():
    try:
        page = int(request.args.get("page", 1))
        per_page = 10
        skip = (page - 1) * per_page
        data = list(collection.find().sort("timestamp", -1).skip(skip).limit(per_page))
        result = []
        
        for d in data:
            # Convert MongoDB datetime to ISO string format
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
        return jsonify({"error": str(e)}), 500

@app.route("/latest")
def get_latest():
    try:
        data = list(collection.find().sort("timestamp", -1).limit(20))
        result = []
        
        for d in reversed(data):
            # Convert MongoDB datetime to ISO string format
            timestamp = d["timestamp"]
            if hasattr(timestamp, 'isoformat'):
                # If it's a datetime object, convert to ISO format
                timestamp_str = timestamp.isoformat()
            else:
                # If it's already a string, use as is
                timestamp_str = str(timestamp)
            
            result.append({
                "timestamp": timestamp_str,
                **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
            })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ NEW: Add endpoint for real-time status
@app.route("/status")
def get_status():
    try:
        # Get the most recent entry
        latest_entry = collection.find_one(sort=[("timestamp", -1)])
        
        if latest_entry:
            # Handle MongoDB datetime properly
            timestamp = latest_entry["timestamp"]
            if hasattr(timestamp, 'isoformat'):
                last_update_str = timestamp.isoformat()
                # Calculate time difference
                time_diff = (datetime.now() - timestamp).total_seconds()
            else:
                last_update_str = str(timestamp)
                time_diff = 999  # Set high value if can't calculate
            
            is_online = time_diff < 30  # Consider online if data is less than 30 seconds old
            
            return jsonify({
                "mpu1_online": is_online,
                "mpu2_online": is_online,
                "last_update": last_update_str,
                "seconds_ago": int(time_diff),
                "total_records": collection.count_documents({})
            })
        else:
            return jsonify({
                "mpu1_online": False,
                "mpu2_online": False,
                "last_update": None,
                "seconds_ago": None,
                "total_records": 0
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def home():
    return "Flask API for 2x MPU6050 via MQTT (12 fields only) - Fixed Timestamps ✅"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
