from flask import Flask, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime
import threading
import time
import random

app = Flask(__name__)
CORS(app)  # ✅ ให้ Frontend เรียก API ได้ข้ามโดเมน

# ✅ MongoDB Atlas URI
uri = "mongodb+srv://projectEE:ee707778@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(uri)
db = client["sensor_db"]
collection = db["sensor_data"]

# ✅ ล้าง collection ตอนเริ่มใหม่ (เพื่อความสะอาด)
collection.delete_many({})

# ✅ ฟังก์ชันสุ่มข้อมูล sensor แล้วส่งเข้า MongoDB
def simulate_sensor():
    while True:
        data = {
            "timestamp": datetime.now(),
            "temperature": round(random.uniform(24.0, 36.0), 2),
            "humidity": round(random.uniform(45.0, 85.0), 2)
        }
        print("🔄 Sending fake data:", data)
        collection.insert_one(data)
        time.sleep(1)  # ส่งทุก 1 วินาที

# ✅ เริ่ม thread ส่งข้อมูลจำลอง (background)
threading.Thread(target=simulate_sensor, daemon=True).start()

@app.route("/")
def index():
    return "✅ API is running. Access /data to view sensor data."

# ✅ API ดึงข้อมูลล่าสุดจาก MongoDB
@app.route("/data")
def get_data():
    data = list(collection.find().sort("timestamp", -1).limit(10))
    return jsonify([
        {
            "timestamp": d["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
            "temperature": d["temperature"],
            "humidity": d["humidity"]
        }
        for d in data
    ])

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
