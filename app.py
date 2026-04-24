from __future__ import annotations

import argparse
import base64
import logging
import struct
import threading
import time

import cv2
import yaml
from flask import Flask, Response, render_template, request
from flask_socketio import SocketIO

from analytics import AnalyticsMetrics, StatisticalQueuePredictor, calculate_density
from network_simulator import net_sim
from processor import EdgeProcessor
from stream_manager import VideoStream, resolve_camera_source

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


with open("config.yaml", "r", encoding="utf-8") as file:
    config = yaml.safe_load(file)

parser = argparse.ArgumentParser(description="Run 5G Edge Analytics App", add_help=False)
parser.add_argument("--camera-ip", type=str, help="IP address of the real camera (overrides config and disables auto-scan)")
args, _ = parser.parse_known_args()

USE_REAL_CAMERA: bool = config.get("use_real_camera", False)

if USE_REAL_CAMERA:
    camera_url = config.get("camera_url", "")
    camera_http_url = config.get("camera_http_url", "")
    auto_discovery = config.get("camera_auto_discovery", {})
    camera_rtsp_transport = config.get("camera_rtsp_transport", "tcp")

    if args.camera_ip:
        from camera_finder import swap_ip_in_url
        if camera_url:
            camera_url = swap_ip_in_url(camera_url, args.camera_ip) or camera_url
        if camera_http_url:
            camera_http_url = swap_ip_in_url(camera_http_url, args.camera_ip) or camera_http_url
        auto_discovery["enabled"] = False
        config["real_latency_probe_host"] = args.camera_ip

    VIDEO_SOURCE, resolved_camera_host = resolve_camera_source(
        camera_url,
        camera_http_url,
        auto_discovery=auto_discovery,
    )

    if auto_discovery.get("sync_probe_host", True) and resolved_camera_host:
        config["real_latency_probe_host"] = resolved_camera_host

    logger.info(f"[App] REAL CAMERA mode - resolved source: {VIDEO_SOURCE}")
else:
    VIDEO_SOURCE = config.get("video_source", 0)
    logger.info(f"[App] SIMULATED mode - source: {VIDEO_SOURCE}")


app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

lock = threading.Lock()
current_raw_frame = None
current_stats = AnalyticsMetrics(0, 0, 0.0, 0.0)
queue_predictors: dict[str, StatisticalQueuePredictor] = {}


if USE_REAL_CAMERA:
    probe_host = config.get("real_latency_probe_host", "")
    probe_interval = config.get("latency_probe_interval_sec", 1.0)
    net_sim.enable_real_camera_mode(
        probe_host=probe_host,
        probe_interval_sec=probe_interval,
    )
    logger.info(
        f"[App] Real latency probe: host='{probe_host}' interval={probe_interval}s"
    )


@app.route("/set_profile/<profile_name>")
def set_profile(profile_name):
    """
    API to dynamically change the network conditions shown by the dashboard.

    In real-camera mode this does not add artificial delay to the pipeline, but
    it still updates the profile label surfaced in the UI.
    """
    net_sim.set_profile(profile_name)
    return {
        "status": "success",
        "profile": profile_name,
        "real_camera_mode": USE_REAL_CAMERA,
    }


@app.route("/network_status")
def network_status():
    """
    Exposes the current measured network conditions to the dashboard.

    In real-camera mode these are live values, not simulated ones.
    """
    return {
        "mode": "real_camera" if USE_REAL_CAMERA else "simulated",
        "profile": net_sim.profile_name,
        "latency_ms": round(net_sim.current_latency_ms, 2),
        "drop_prob": round(net_sim.current_drop_prob, 4),
    }


def ingest_producer():
    """
    High-speed video ingest thread.

    Frames are captured as fast as possible and published to two places:
    1. The latest raw frame shared with the inference thread
    2. A compressed browser preview over WebSocket
    """
    global current_raw_frame
    stream = VideoStream(
        src=VIDEO_SOURCE,
        rtsp_transport=config.get("camera_rtsp_transport", "tcp"),
    ).start()

    while True:
        raw_frame = stream.read()
        if raw_frame is None:
            socketio.sleep(0.01)
            continue

        with lock:
            current_raw_frame = raw_frame.copy()

        if USE_REAL_CAMERA:
            frame_gap_ms = stream.last_frame_latency_ms
            if frame_gap_ms > 33:
                logger.debug(
                    f"[Ingest] 5G frame gap: {frame_gap_ms:.1f}ms "
                    f"(effective: {1000 / max(frame_gap_ms, 1):.1f} FPS)"
                )
        else:
            net_sim.simulate_delay()
            if net_sim.should_drop_packet():
                continue

        ret, buffer = cv2.imencode(
            ".jpg",
            raw_frame,
            [cv2.IMWRITE_JPEG_QUALITY, 50],
        )
        if not ret:
            continue

        b64_img = base64.b64encode(buffer).decode("utf-8")
        socketio.emit(
            "video_frame",
            {
                "image": b64_img,
                "latency_ms": round(net_sim.current_latency_ms, 1),
                "real_camera": USE_REAL_CAMERA,
            },
        )
        socketio.sleep(0.01)


def inference_consumer():
    """
    Asynchronous AI inference thread.

    It always pulls the newest raw frame so slower inference does not block the
    video ingest path.
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
                StatisticalQueuePredictor(alpha=0.2),
            )

            zone_density = calculate_density(
                queue_zone["people_detected"],
                queue_zone["area_sqm"],
            )
            zone_wait_sec = predictor.predict_wait(
                queue_zone["people_detected"],
                queue_zone["people_in_queue"],
                queue_zone.get("lambda_rate", 0.0),
            )

            total_people_in_queue_zones += queue_zone["people_detected"]
            total_people_queued += queue_zone["people_in_queue"]
            total_queue_area_sqm += queue_zone["area_sqm"]
            max_queue_wait_sec = max(max_queue_wait_sec, zone_wait_sec)

            queue_zone_metrics.append(
                {
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
                }
            )

        density = calculate_density(total_people_in_queue_zones, total_queue_area_sqm)
        wait_sec = max_queue_wait_sec

        current_stats = AnalyticsMetrics(
            logic_data["total_people"],
            total_people_queued,
            density,
            wait_sec,
        )

        binary_payload = struct.pack(
            "!2i2f",
            current_stats.total_people_detected,
            current_stats.people_in_queue,
            current_stats.density,
            current_stats.estimated_wait,
        )

        socketio.emit("telemetry_stream", binary_payload)

        socketio.emit(
            "ai_metadata",
            {
                "boxes": logic_data["boxes"],
                "queue_zones": queue_zone_metrics,
                "roi": queue_zone_metrics[0]["polygon"] if queue_zone_metrics else [],
                "aggregate_wait_mode": "max_queue_wait",
                "network": {
                    "latency_ms": round(net_sim.current_latency_ms, 1),
                    "drop_prob": round(net_sim.current_drop_prob, 4),
                    "real_camera": USE_REAL_CAMERA,
                    "profile": net_sim.profile_name,
                },
            },
        )

        socketio.sleep(0.001)


@app.route("/")
def dashboard_view():
    """Serves the frontend dashboard."""
    return render_template("index.html")


if __name__ == "__main__":
    socketio.start_background_task(ingest_producer)
    socketio.start_background_task(inference_consumer)

    mode_str = "REAL 5G CAMERA" if USE_REAL_CAMERA else "SIMULATED"
    print(f"[*] Started Edge Analytics Node: {config['node_id']}  [{mode_str}]")
    if USE_REAL_CAMERA:
        print(f"[*] Camera URL : {VIDEO_SOURCE}")
        print(f"[*] Probe host : {config.get('real_latency_probe_host', 'N/A')}")
    print("[*] Dashboard accessible at: http://localhost:5000")

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
