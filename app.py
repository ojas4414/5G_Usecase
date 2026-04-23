from flask import Flask, Response, render_template, request
from flask_socketio import SocketIO
import threading
import cv2
import time
import struct
import base64
import logging

from stream_manager import VideoStream, resolve_camera_source
from processor import EdgeProcessor
from analytics import AnalyticsMetrics, calculate_density, StatisticalQueuePredictor
from network_simulator import net_sim
import yaml

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)   # Mute generic Flask noise

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

USE_REAL_CAMERA: bool = config.get("use_real_camera", False)

# Pick the correct video source based on the mode flag
if USE_REAL_CAMERA:
    _camera_url      = config.get("camera_url", "")
    _camera_http_url = config.get("camera_http_url", "")
    # resolve_camera_source tests RTSP first, then HTTP MJPEG automatically
    VIDEO_SOURCE = resolve_camera_source(_camera_url, _camera_http_url)
    logger.info(f"[App] REAL CAMERA mode — resolved source: {VIDEO_SOURCE}")
else:
    VIDEO_SOURCE = config.get("video_source", 0)
    logger.info(f"[App] SIMULATED mode — source: {VIDEO_SOURCE}")

# ─────────────────────────────────────────────────────────────────────────────
# Flask + SocketIO
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
# Concept: URLLC Protocol Upgrade (Phase 3)
# Switch from REST HTTP to asynchronous WebSockets for low-latency telemetry
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

lock = threading.Lock()
current_raw_frame = None
current_stats = AnalyticsMetrics(0, 0, 0.0, 0.0)

# Instantiate Statistical Estimators for predicting Queue Behaviour.
# A separate predictor is maintained per configured queue zone.
queue_predictors = {}

# ─────────────────────────────────────────────────────────────────────────────
# Startup: activate real-camera mode if configured
# ─────────────────────────────────────────────────────────────────────────────
if USE_REAL_CAMERA:
    probe_host = config.get("real_latency_probe_host", "")
    probe_interval = config.get("latency_probe_interval_sec", 1.0)
    net_sim.enable_real_camera_mode(
        probe_host=probe_host,
        probe_interval_sec=probe_interval
    )
    logger.info(
        f"[App] Real latency probe: host='{probe_host}' interval={probe_interval}s"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/set_profile/<profile_name>')
def set_profile(profile_name):
    """
    API to dynamically change the 5G network conditions.
    In real-camera mode this has no effect on the video pipeline (no artificial
    delays are injected), but it does update profile_name for the UI badge.
    """
    net_sim.set_profile(profile_name)
    return {
        "status": "success",
        "profile": profile_name,
        "real_camera_mode": USE_REAL_CAMERA
    }


@app.route('/network_status')
def network_status():
    """
    Exposes current measured network conditions to the dashboard.
    In real-camera mode these are live-measured values, not simulated ones.
    """
    return {
        "mode": "real_camera" if USE_REAL_CAMERA else "simulated",
        "profile": net_sim.profile_name,
        "latency_ms": round(net_sim.current_latency_ms, 2),
        "drop_prob": round(net_sim.current_drop_prob, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Background Threads
# ─────────────────────────────────────────────────────────────────────────────

def ingest_producer():
    """
    High-speed Video Ingest Thread.
    Grabs frames from the camera as fast as possible and makes them available
    to the inference consumer. Also forwards a JPEG-compressed copy over the
    WebSocket for live browser preview.

    KEY 5G CAMERA DIFFERENCE:
    - In simulated mode: net_sim.simulate_delay() + should_drop_packet() are called
      here to artificially recreate 5G conditions.
    - In real-camera mode: those calls are SKIPPED entirely. The real 5G link
      already introduces genuine latency and packet loss; adding artificial sleep
      on top would double the delay and misrepresent the system's true performance.
      The VideoStream ring-buffer (maxsize=1) ensures only the freshest frame is
      ever processed regardless of how the link behaves.
    """
    global current_raw_frame
    stream = VideoStream(src=VIDEO_SOURCE).start()

    while True:
        raw_frame = stream.read()
        if raw_frame is None:
            socketio.sleep(0.01)
            continue

        with lock:
            current_raw_frame = raw_frame.copy()

        if USE_REAL_CAMERA:
            # ── Real 5G Camera Path ────────────────────────────────────────
            # No artificial delay or packet-drop simulation.
            # The network already provides real conditions.
            # We DO emit real frame-gap latency in the telemetry for logging.
            frame_gap_ms = stream.last_frame_latency_ms
            if frame_gap_ms > 33:   # > 33ms = below 30fps = link stress
                logger.debug(
                    f"[Ingest] 5G frame gap: {frame_gap_ms:.1f}ms "
                    f"(effective: {1000/max(frame_gap_ms,1):.1f} FPS)"
                )
        else:
            # ── Simulated Mode Path ───────────────────────────────────────
            delay_ms = net_sim.simulate_delay()
            if net_sim.should_drop_packet():
                # Simulate a dropped UDP packet — skip sending this frame to browser
                continue

        # ── WebSocket video preview ────────────────────────────────────────
        # Quality=50 is deliberately low — we're showing motion, not detail.
        # The AI inference thread operates on the full raw frame independently.
        ret, buffer = cv2.imencode('.jpg', raw_frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if not ret:
            continue

        b64_img = base64.b64encode(buffer).decode('utf-8')
        socketio.emit('video_frame', {
            'image': b64_img,
            'latency_ms': round(net_sim.current_latency_ms, 1),
            'real_camera': USE_REAL_CAMERA,
        })
        socketio.sleep(0.01)


def inference_consumer():
    """
    Asynchronous AI Inference Thread.
    Polls the latest available raw frame, runs YOLO, and emits telemetry.
    Decoupled from ingest_producer so a slow inference run never blocks the
    video feed.

    In both modes, processor.py's adaptive resolution logic
        current_imgsz = 320 if net_sim.current_latency_ms > 50 else 640
    uses the same `net_sim.current_latency_ms` field — which is now populated
    by real ICMP probe results in real-camera mode, or by simulated values in
    simulated mode. No changes needed in processor.py.
    """
    global current_raw_frame, current_stats
    processor = EdgeProcessor()

    # Brief warm-up: give ingest_producer time to fill the first frame
    time.sleep(2.0)

    while True:
        with lock:
            if current_raw_frame is None:
                socketio.sleep(0.01)
                continue
            frame_to_process = current_raw_frame.copy()

        # GPU-Accelerated Inference (doesn't block the video feed)
        logic_data = processor.process_frame(frame_to_process)

        queue_zone_metrics = []
        total_people_in_queue_zones = 0
        total_people_queued = 0
        total_queue_area_sqm = 0.0
        max_queue_wait_sec = 0.0

        for queue_zone in logic_data.get("queue_zones", []):
            zone_id = queue_zone["id"]
            predictor = queue_predictors.setdefault(
                zone_id,
                StatisticalQueuePredictor(alpha=0.2)
            )

            zone_density = calculate_density(
                queue_zone["people_detected"],
                queue_zone["area_sqm"]
            )
            zone_wait_sec = predictor.predict_wait(
                queue_zone["people_detected"],
                queue_zone["people_in_queue"],
                queue_zone.get("lambda_rate", 0.0)
            )

            total_people_in_queue_zones += queue_zone["people_detected"]
            total_people_queued += queue_zone["people_in_queue"]
            total_queue_area_sqm += queue_zone["area_sqm"]
            max_queue_wait_sec = max(max_queue_wait_sec, zone_wait_sec)

            queue_zone_metrics.append({
                "id": queue_zone["id"],
                "name": queue_zone["name"],
                "polygon": queue_zone["polygon"],
                "area_sqm": queue_zone["area_sqm"],
                "queue_wait_threshold_sec": queue_zone["queue_wait_threshold_sec"],
                "color": queue_zone["color"],
                "people_detected": queue_zone["people_detected"],
                "people_in_queue": queue_zone["people_in_queue"],
                "density": zone_density,
                "estimated_wait": zone_wait_sec,
            })

        density = calculate_density(
            total_people_in_queue_zones,
            total_queue_area_sqm
        )
        wait_sec = max_queue_wait_sec

        current_stats = AnalyticsMetrics(
            logic_data["total_people"],
            total_people_queued,
            density,
            wait_sec
        )

        # Binary-packed telemetry for minimal WebSocket overhead
        binary_payload = struct.pack(
            '!2i2f',
            current_stats.total_people_detected,
            current_stats.people_in_queue,
            current_stats.density,
            current_stats.estimated_wait
        )

        socketio.emit('telemetry_stream', binary_payload)

        # Rich AI metadata → browser GPU canvas overlay
        socketio.emit('ai_metadata', {
            "boxes": logic_data["boxes"],
            "queue_zones": queue_zone_metrics,
            "roi": queue_zone_metrics[0]["polygon"] if queue_zone_metrics else [],
            "aggregate_wait_mode": "max_queue_wait",
            # Surface real-time network health alongside AI data
            "network": {
                "latency_ms": round(net_sim.current_latency_ms, 1),
                "drop_prob":  round(net_sim.current_drop_prob, 4),
                "real_camera": USE_REAL_CAMERA,
                "profile": net_sim.profile_name,
            }
        })

        socketio.sleep(0.001)


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard_view():
    """Serves the frontend dashboard."""
    return render_template('index.html')


# DEPRECATED: HTTP MJPEG polling removed in favour of WebSocket pipeline

if __name__ == '__main__':
    socketio.start_background_task(ingest_producer)
    socketio.start_background_task(inference_consumer)

    mode_str = "REAL 5G CAMERA" if USE_REAL_CAMERA else "SIMULATED"
    print(f"[*] Started Edge Analytics Node: {config['node_id']}  [{mode_str}]")
    if USE_REAL_CAMERA:
        print(f"[*] Camera URL : {VIDEO_SOURCE}")
        print(f"[*] Probe host : {config.get('real_latency_probe_host', 'N/A')}")
    print("[*] Dashboard accessible at: http://localhost:5000")

    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
