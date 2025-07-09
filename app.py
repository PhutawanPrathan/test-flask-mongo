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

# ✅ ตัวแปรเก็บค่าจากแต่ละ MPU
latest_data = {
    "mpu1": None,
    "mpu2": None
}

# ✅ Callback รับข้อมูลจาก MQTT
def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        mpu_id = "mpu1" if msg.topic == "sensor/mpu1" else "mpu2"
        latest_data[mpu_id] = payload

        # ถ้ามีครบทั้ง mpu1 และ mpu2 ค่อย insert ลง MongoDB
        if latest_data["mpu1"] and latest_data["mpu2"]:
            combined_data = {
                "timestamp": datetime.now(),
                "mpu1_ax": latest_data["mpu1"]["ax"],
                "mpu1_ay": latest_data["mpu1"]["ay"],
                "mpu1_az": latest_data["mpu1"]["az"],
                "mpu1_gx": latest_data["mpu1"]["gx"],
                "mpu1_gy": latest_data["mpu1"]["gy"],
                "mpu1_gz": latest_data["mpu1"]["gz"],
                "mpu2_ax": latest_data["mpu2"]["ax"],
                "mpu2_ay": latest_data["mpu2"]["ay"],
                "mpu2_az": latest_data["mpu2"]["az"],
                "mpu2_gx": latest_data["mpu2"]["gx"],
                "mpu2_gy": latest_data["mpu2"]["gy"],
                "mpu2_gz": latest_data["mpu2"]["gz"]
            }
            collection.insert_one(combined_data)
            latest_data["mpu1"] = None
            latest_data["mpu2"] = None
            print("✅ Inserted:", combined_data)

    except Exception as e:
        print("❌ Error:", e)

def mqtt_thread():
    mqtt_client = mqtt.Client()
    mqtt_client.on_message = on_message
    mqtt_client.connect("localhost", 1883, 60)
    mqtt_client.subscribe("sensor/mpu1")
    mqtt_client.subscribe("sensor/mpu2")
    mqtt_client.loop_forever()

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
    return "Flask API for 2x MPU6050 via MQTT"

if __name__ == "__main__":
    app.run(debug=True)
