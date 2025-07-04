from flask import Flask, jsonify
from pymongo import MongoClient
from datetime import datetime
import threading
import time
import random
from flask_cors import CORS

app = Flask(__name__)
CORS(app) # ✅ อนุญาตให้ Netlify หรือเว็บอื่นๆ เรียก API ได้
# ✅ MongoDB URI
uri = "mongodb+srv://projectEE:ee707778@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(uri)
db = client["sensor_db"]
collection = db["sensor_data"]

# ✅ ฟังก์ชันจำลองการส่งข้อมูลเข้า MongoDB
def simulate_sensor():
    while True:
        data = {
            "timestamp": datetime.now(),
            "accel_x": round(random.uniform(-2.0, 2.0), 3),
            "accel_y": round(random.uniform(-2.0, 2.0), 3),
            "accel_z": round(random.uniform(8.5, 10.5), 3),  # ค่า z ~ 9.8 m/s² (gravity)
            "gyro_x": round(random.uniform(-250.0, 250.0), 3),
            "gyro_y": round(random.uniform(-250.0, 250.0), 3),
            "gyro_z": round(random.uniform(-250.0, 250.0), 3)
        }
        print("📡 Simulated MPU6050 data:", data)
        collection.insert_one(data)
        time.sleep(1)


# ✅ เริ่ม thread background
threading.Thread(target=simulate_sensor, daemon=True).start()

@app.route("/")
def index():
    return "API is running. Go to /data to view data."

# ✅ API สำหรับส่งข้อมูล (Frontend จะเรียกจาก Netlify)
@app.route("/data")
def get_data():
    data = list(collection.find().sort("timestamp", -1).limit(10))
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

if __name__ == "__main__":
    app.run(debug=True)
