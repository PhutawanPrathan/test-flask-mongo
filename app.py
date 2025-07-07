# app.py (Flask Backend)
from flask import Flask, jsonify, request
from pymongo import MongoClient
from datetime import datetime
import threading
import time
import random
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

uri = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(uri)
db = client["sensor_db"]
collection = db["sensor_data"]

# ✅ Mock sensor data
def simulate_sensor():
    while True:
        data = {
            "timestamp": datetime.now(),
            "accel_x": round(random.uniform(-2.0, 2.0), 3),
            "accel_y": round(random.uniform(-2.0, 2.0), 3),
            "accel_z": round(random.uniform(8.5, 10.5), 3),
            "gyro_x": round(random.uniform(-250.0, 250.0), 3),
            "gyro_y": round(random.uniform(-250.0, 250.0), 3),
            "gyro_z": round(random.uniform(-250.0, 250.0), 3)
        }
        collection.insert_one(data)
        time.sleep(1)

threading.Thread(target=simulate_sensor, daemon=True).start()

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
                "accel_x": d["accel_x"],
                "accel_y": d["accel_y"],
                "accel_z": d["accel_z"],
                "gyro_x": d["gyro_x"],
                "gyro_y": d["gyro_y"],
                "gyro_z": d["gyro_z"]
            }
            for d in data
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/latest")
def get_latest():
    data = list(collection.find().sort("timestamp", -1).limit(20))
    return jsonify([
        {
            "timestamp": d["timestamp"].strftime("%H:%M:%S"),
            "accel_x": d["accel_x"],
            "accel_y": d["accel_y"],
            "accel_z": d["accel_z"],
            "gyro_x": d["gyro_x"],
            "gyro_y": d["gyro_y"],
            "gyro_z": d["gyro_z"]
        }
        for d in reversed(data)
    ])

@app.route("/")
def home():
    return "Flask API for MPU6050 Mock Data"

if __name__ == "__main__":
    app.run(debug=True)
