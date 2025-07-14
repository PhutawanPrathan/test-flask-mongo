from flask import Flask, jsonify, request
from pymongo import MongoClient
from datetime import datetime
import threading
import json
import paho.mqtt.client as mqtt
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

uri = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(uri)
db = client["sensor_db"]
collection = db["sensor_data"]

latest_data = {
    "mpu1": None,
    "mpu2": None
}

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())

        if msg.topic == "esp32/mpu1":
            mpu_id = "mpu1"
        elif msg.topic == "esp32/mpu2":
            mpu_id = "mpu2"
        else:
            return  # ไม่สนใจ topic อื่น

        latest_data[mpu_id] = payload

        if latest_data["mpu1"] and latest_data["mpu2"]:
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
            time.sleep(5)  # wait before reconnect


threading.Thread(target=mqtt_thread, daemon=True).start()

@app.route("/data")
def get_data():
    try:
        page = int(request.args.get("page", 1))
        per_page = 10
        skip = (page - 1) * per_page
        data = list(collection.find().sort("timestamp", -1).skip(skip).limit(per_page))
        return jsonify([
            {
                "timestamp": d["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
            } for d in data
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/latest")
def get_latest():
    data = list(collection.find().sort("timestamp", -1).limit(20))
    return jsonify([
        {
            "timestamp": d["timestamp"].strftime("%H:%M:%S"),
            **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
        } for d in reversed(data)
    ])

@app.route("/")
def home():
    return "Flask API for 2x MPU6050 via MQTT (12 fields only)"

if __name__ == "__main__":
    app.run(debug=True)
