
from flask import Flask, Response, render_template, request
from flask_socketio import SocketIO
import threading
import cv2
import time
import struct
from stream_manager import VideoStream
from processor import EdgeProcessor
from analytics import AnalyticsMetrics, calculate_density, StatisticalQueuePredictor
from network_simulator import net_sim
import yaml
import logging

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) # Mute generic flask logging for our project

with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

app = Flask(__name__)
# Concept: URLLC Protocol Upgrade (Phase 3)
# Switch from REST HTTP to asynchronous WebSockets for low-latency telemetry
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

lock = threading.Lock()
current_raw_frame = None
current_stats = AnalyticsMetrics(0, 0, 0.0, 0.0)

# Instantiate the Statistical Estimator for predicting Queue Behavior over the YOLO pipeline
queue_predictor = StatisticalQueuePredictor(alpha=0.2)

@app.route('/set_profile/<profile_name>')
def set_profile(profile_name):
    """API to dynamically change the 5G network conditions"""
    net_sim.set_profile(profile_name)
    return {"status": "success", "profile": profile_name}

def ingest_producer():
    """
    High-speed Video Thread. Grabs directly from camera stream buffers and immediately multiplexes.
    Zero AI blocking.
    """
    global current_raw_frame
    stream = VideoStream(src=config["video_source"]).start()
    
    while True:
        raw_frame = stream.read()
        if raw_frame is None:
            socketio.sleep(0.01)
            continue
            
        with lock:
            current_raw_frame = raw_frame.copy()
            
        delay_ms = net_sim.simulate_delay()
        if net_sim.should_drop_packet():
            continue
            
        ret, buffer = cv2.imencode('.jpg', raw_frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if not ret:
            continue
            
        import base64
        b64_img = base64.b64encode(buffer).decode('utf-8')
        socketio.emit('video_frame', {'image': b64_img})
        socketio.sleep(0.01)

def inference_consumer():
    """
    Asynchronous AI Thread. Polls the latest available frame, drops unread frames (natural scaling).
    """
    global current_raw_frame, current_stats
    processor = EdgeProcessor()
    
    time.sleep(2.0)
    
    while True:
        with lock:
            if current_raw_frame is None:
                socketio.sleep(0.01)
                continue
            frame_to_process = current_raw_frame.copy()
            
        # GPU Accelerated Inference (Doesn't block video feed!)
        logic_data = processor.process_frame(frame_to_process)
        
        density = calculate_density(logic_data["total_people"], config.get('roi_area_sqm', 10.0))
        wait_sec = queue_predictor.predict_wait(logic_data["total_people"], logic_data["people_in_queue"], logic_data.get("lambda_rate", 0.0))
        
        current_stats = AnalyticsMetrics(logic_data["total_people"], logic_data["people_in_queue"], density, wait_sec)
        
        binary_payload = struct.pack('!2i2f', current_stats.total_people_detected, current_stats.people_in_queue, current_stats.density, current_stats.estimated_wait)
        
        socketio.emit('telemetry_stream', binary_payload)
        # Emit raw metadata to browser GPU
        socketio.emit('ai_metadata', {"boxes": logic_data["boxes"], "roi": config["roi_polygon"]})
        
        socketio.sleep(0.001)

@app.route('/')
def dashboard_view():
    """Serves the frontend Bootstrap HTML"""
    return render_template('index.html')

# DEPRECATED: HTTP MJPEG polling removed in favor of Websocket Pipeline

if __name__ == '__main__':
    socketio.start_background_task(ingest_producer)
    socketio.start_background_task(inference_consumer)
    
    print(f"[*] Started Edge Analytics Node: {config['node_id']}")
    print("[*] Dashboard accessible at: http://localhost:5000")
    
    # Run the socketio app
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
