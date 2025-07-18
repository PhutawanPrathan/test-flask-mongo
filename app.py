from flask import Flask, render_template, Response, jsonify, request
from pymongo import MongoClient
from datetime import datetime
import threading
import time
import json
import requests
import numpy as np
import cv2
import paho.mqtt.client as mqtt
from ultralytics import YOLO
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# MongoDB
MONGO_URI = "mongodb+srv://projectEE:ee707178@cluster0.ttq1nzx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["sensor_db"]
sensor_collection = db["sensor_data"]
camera_collection = db["mm3"]

sensor_collection.create_index("timestamp", expireAfterSeconds=100)

# === ESP32 CAM + YOLO ===
ESP32_CAM_URL = "http://192.168.4.2/capture"
REAL_WIDTH_CM = 0.8
FOCAL_LENGTH_PX = 1000
model = YOLO("mix(320x160).pt")

latest_frame = None
raw_frame = None
detections_data = []
frame_lock = threading.Lock()

def estimate_distance(real_width_cm, focal_length_px, width_px):
    return (real_width_cm * focal_length_px) / width_px if width_px > 0 else 0

def fetch_frame():
    global raw_frame
    while True:
        try:
            response = requests.get(ESP32_CAM_URL, timeout=0.3)
            if response.status_code == 200:
                img_array = np.frombuffer(response.content, np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                with frame_lock:
                    raw_frame = frame
        except Exception as e:
            print("❌ Fetch error:", e)
        time.sleep(0.05)

def infer_frame():
    global latest_frame, detections_data
    while True:
        try:
            with frame_lock:
                frame = raw_frame.copy() if raw_frame is not None else None

            if frame is not None:
                resized = cv2.resize(frame, (320, 160))
                results = model(resized, imgsz=160, conf=0.5)[0]
                current_detections = []

                for box in results.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    label = model.names[cls]
                    width_px = float(x2 - x1)
                    distance_cm = estimate_distance(REAL_WIDTH_CM, FOCAL_LENGTH_PX, width_px)

                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    current_detections.append({
                        "timestamp": timestamp,
                        "label": label,
                        "confidence": float(round(conf, 4)),
                        "distance_cm": float(round(distance_cm, 2))
                    })

                    text = f"{label} {conf*100:.1f}% {distance_cm:.1f}cm"
                    cv2.rectangle(resized, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.putText(resized, text, (int(x1), int(y2) + 15),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

                detections_data = current_detections
                ret, jpeg = cv2.imencode('.jpg', resized)
                latest_frame = jpeg.tobytes()
        except Exception as e:
            print("❌ Inference error:", e)
        time.sleep(0.05)

def upload_to_mongo():
    global detections_data
    while True:
        try:
            if detections_data:
                camera_collection.insert_one({
                    "upload_time": datetime.now(),
                    "detections": detections_data
                })
                print(f"✅ Uploaded {len(detections_data)} detections to MongoDB")
        except Exception as e:
            print("❌ MongoDB upload error:", e)
        time.sleep(1)

# === MQTT + MPU6050 ===
latest_data = {"mpu1": None, "mpu2": None}
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
                sensor_collection.insert_one({
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
                })
                print("✅ Sensor data saved.")
                last_sent_time = current_time
            latest_data["mpu1"] = None
            latest_data["mpu2"] = None
    except Exception as e:
        print("❌ Sensor MQTT error:", e)

def mqtt_thread():
    mqtt_client = mqtt.Client()
    mqtt_client.on_message = on_message
    while True:
        try:
            mqtt_client.connect("localhost", 1883, 60)
            mqtt_client.subscribe("esp32/mpu1")
            mqtt_client.subscribe("esp32/mpu2")
            print("🚀 MQTT connected")
            mqtt_client.loop_forever()
        except Exception as e:
            print("❌ MQTT reconnecting:", e)
            time.sleep(5)

# === ROUTES ===
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/video_feed")
def video_feed():
    def generate():
        while True:
            if latest_frame:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
            time.sleep(0.03)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/latest")
def get_latest():
    data = list(sensor_collection.find().sort("timestamp", -1).limit(20))
    return jsonify([
        {
            "timestamp": d["timestamp"].strftime("%H:%M:%S"),
            **{k: d.get(k) for k in d if k != "_id" and k != "timestamp"}
        } for d in reversed(data)
    ])

@app.route("/esp32_detections")
def esp32_detections():
    try:
        doc = camera_collection.find_one(sort=[("upload_time", -1)])
        return jsonify(doc.get("detections", [])) if doc else jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# === START THREADS ===
if __name__ == '__main__':
    threading.Thread(target=mqtt_thread, daemon=True).start()
    threading.Thread(target=fetch_frame, daemon=True).start()
    threading.Thread(target=infer_frame, daemon=True).start()
    threading.Thread(target=upload_to_mongo, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
