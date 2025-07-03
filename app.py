from flask import Flask, jsonify
from pymongo import MongoClient
from datetime import datetime
import threading
import time
import random

app = Flask(__name__)

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
            "temperature": round(random.uniform(25, 35), 2),
            "humidity": random.randint(50, 80)
        }
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
            "temperature": d["temperature"],
            "humidity": d["humidity"]
        }
        for d in data
    ])

if __name__ == "__main__":
    app.run(debug=True)
