import time
import random
import cv2
import yaml
import numpy as np
import torch
from ultralytics import YOLO
from shapely.geometry import Point, Polygon
from collections import defaultdict
import logging
from network_simulator import net_sim

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(threadName)s: %(message)s")

# Concept: Centralized configuration parsing
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

QUEUE_ZONE_COLORS = [
    "#0f766e",
    "#b45309",
    "#2563eb",
    "#7c3aed",
    "#dc2626",
    "#0891b2",
]

class EdgeProcessor:
    """
    Concept: 
    1. AI Inference Subsystem (YOLOv8)
    2. GPU Device Auto-Selection (CUDA)
    3. Spatial State Management (Shapely/Dictionary)
    """
    def __init__(self, model_path="yolov8n.onnx"):
        # Concept: CUDA Device Auto-Selection
        # torch.cuda.is_available() queries the NVIDIA driver to confirm CUDA is usable.
        # If a CUDA GPU is found, we assign 'cuda' as our device — PyTorch will then
        # allocate model weights and inference tensors directly into GPU VRAM instead of RAM.
        # This is the single most impactful performance change possible for an AI application.
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logging.info(f"EdgeProcessor using device: {self.device.upper()}")
        if self.device == 'cuda':
            logging.info(f"GPU: {torch.cuda.get_device_name(0)} | VRAM: {torch.cuda.get_device_properties(0).total_memory // 1024**2} MB")
        
        # Load the YOLO model (ONNX runtime auto-detects CUDA/CPU providers internally)
        self.model = YOLO(model_path)
        
        # Concept: Spatial Geometry boundary
        self.queue_zones = self._load_queue_zones(config)
        self.track_grace_period_sec = config.get("track_grace_period_sec", 2.0)
        
        # Concept: Tracking State Persistence
        # Keeps the current queue-zone assignment and the timestamp each tracked
        # person entered that specific zone.
        self.track_states = {}
        
        # Concept: Empirical Service Times (Phase 4)
        self.recent_service_times = defaultdict(list)

    def _load_queue_zones(self, config: dict) -> list[dict]:
        """
        Supports two config shapes:
        1. queue_zones: [{id, name, polygon, area_sqm, queue_wait_threshold_sec}]
        2. Legacy roi_polygon / roi_area_sqm fallback for single-queue mode
        """
        raw_zones = config.get("queue_zones")
        default_wait_threshold = float(config.get("queue_wait_threshold_sec", 5.0))
        default_area = float(config.get("roi_area_sqm", 10.0))

        if raw_zones:
            zones = []
            for index, zone_config in enumerate(raw_zones):
                polygon = zone_config.get("polygon")
                if not polygon or len(polygon) < 3:
                    raise ValueError(
                        f"queue_zones[{index}] must define a polygon with at least 3 points"
                    )

                zones.append({
                    "id": zone_config.get("id", f"queue_{index + 1}"),
                    "name": zone_config.get("name", f"Queue {index + 1}"),
                    "polygon": polygon,
                    "shape": Polygon(polygon),
                    "area_sqm": float(zone_config.get("area_sqm", default_area)),
                    "queue_wait_threshold_sec": float(
                        zone_config.get("queue_wait_threshold_sec", default_wait_threshold)
                    ),
                    "color": zone_config.get(
                        "color",
                        QUEUE_ZONE_COLORS[index % len(QUEUE_ZONE_COLORS)],
                    ),
                })
            return zones

        # Backward-compatible single-zone fallback
        fallback_polygon = config.get("roi_polygon")
        if not fallback_polygon or len(fallback_polygon) < 3:
            raise ValueError("config.yaml must define either queue_zones or roi_polygon")

        return [{
            "id": "main_queue",
            "name": config.get("queue_name", "Main Queue"),
            "polygon": fallback_polygon,
            "shape": Polygon(fallback_polygon),
            "area_sqm": default_area,
            "queue_wait_threshold_sec": default_wait_threshold,
            "color": QUEUE_ZONE_COLORS[0],
        }]

    def _find_queue_zone(self, center_x: int, center_y: int) -> dict | None:
        """
        Resolves the first queue zone whose polygon covers the detection point.
        Queue order in config.yaml determines precedence when polygons overlap.
        """
        point = Point(center_x, center_y)
        for zone in self.queue_zones:
            if zone["shape"].covers(point):
                return zone
        return None

    def _record_service_time(self, track_state: dict, exit_time: float) -> None:
        zone_id = track_state.get("zone_id")
        entered_at = track_state.get("entered_at")

        if not zone_id or entered_at is None:
            return

        zone = next((item for item in self.queue_zones if item["id"] == zone_id), None)
        if zone is None:
            return

        time_spent = max(0.0, exit_time - entered_at)
        if time_spent <= zone["queue_wait_threshold_sec"]:
            return

        service_times = self.recent_service_times[zone_id]
        service_times.append(time_spent)
        if len(service_times) > 20:
            service_times.pop(0)

    def process_frame(self, frame: np.ndarray) -> dict:
        """
        Runs the YOLO model, updates queue logic, and annotates the frame.
        """
        process_start_time = time.time()
        
        # Concept: Adaptive Computation Offloading (Phase 2)
        # If the 5G slice is congested (latency > 50ms), we scale down YOLO bounding to 320x320
        # to maximize FPS and offload networking pressure.
        current_imgsz = 320 if net_sim.current_latency_ms > 50 else 640
        
        # 1. Run inference using BoT-SORT / ByteTrack via persist=True
        # This gives us unique IDs for people across frames.
        # Accelerated ONNX Graph Inference
        results = self.model.track(frame, persist=True, classes=[0], verbose=False, imgsz=current_imgsz)
        
        total_people = 0
        now = time.time()
        metadata_boxes = []
        zone_metrics = {
            zone["id"]: {
                "id": zone["id"],
                "name": zone["name"],
                "polygon": zone["polygon"],
                "area_sqm": zone["area_sqm"],
                "queue_wait_threshold_sec": zone["queue_wait_threshold_sec"],
                "color": zone["color"],
                "people_detected": 0,
                "people_in_queue": 0,
                "lambda_rate": 0.0,
            }
            for zone in self.queue_zones
        }

        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.cpu().numpy().astype(int)
            total_people = len(track_ids)
            
            # Temporary list to track who we saw this exact frame
            active_ids_in_zones = set()
            
            for box, track_id in zip(boxes, track_ids):
                x1, y1, x2, y2 = map(int, box)
                
                # Concept: Spatial Geometry (Center Point of Bounding Box)
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                matched_zone = self._find_queue_zone(center_x, center_y)

                # Check if the person's center is inside one of the configured queue
                # polygons. This allows the same camera to monitor multiple lanes.
                if matched_zone is not None:
                    zone_id = matched_zone["id"]
                    active_ids_in_zones.add(track_id)
                    zone_state = self.track_states.get(track_id)

                    if zone_state is None:
                        zone_state = {
                            "zone_id": zone_id,
                            "entered_at": now,
                            "last_zone_seen_at": now,
                        }
                        self.track_states[track_id] = zone_state
                    elif zone_state["zone_id"] != zone_id:
                        self._record_service_time(
                            zone_state,
                            zone_state["last_zone_seen_at"],
                        )
                        zone_state["zone_id"] = zone_id
                        zone_state["entered_at"] = now
                        zone_state["last_zone_seen_at"] = now
                    else:
                        zone_state["last_zone_seen_at"] = now

                    time_in_zone = now - zone_state["entered_at"]
                    zone_metrics[zone_id]["people_detected"] += 1

                    if time_in_zone > matched_zone["queue_wait_threshold_sec"]:
                        zone_metrics[zone_id]["people_in_queue"] += 1
                        status = "queued"
                    else:
                        status = "waiting"

                    metadata_boxes.append({
                        "id": int(track_id),
                        "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
                        "cx": int(center_x), "cy": int(center_y),
                        "status": status,
                        "time": int(time_in_zone),
                        "queue_zone_id": zone_id,
                        "queue_zone_name": matched_zone["name"],
                        "color": matched_zone["color"],
                    })

            # Cleanup tracking state for people who left all queue zones
            # (Prevents memory leaks over hundreds of hours on a server)
            for old_id in list(self.track_states.keys()):
                if old_id not in active_ids_in_zones:
                    # Check against the grace period to avoid dropping IDs that YOLO momentarily lost
                    time_since_last_seen = now - self.track_states[old_id]["last_zone_seen_at"]
                    if time_since_last_seen > self.track_grace_period_sec:
                        self._record_service_time(
                            self.track_states[old_id],
                            self.track_states[old_id]["last_zone_seen_at"],
                        )
                        del self.track_states[old_id]

        # Calculate dynamic Lambda service rate per queue zone (Phase 4)
        for zone_id, service_times in self.recent_service_times.items():
            if len(service_times) > 0 and zone_id in zone_metrics:
                avg_service = sum(service_times) / len(service_times)
                if avg_service > 0:
                    zone_metrics[zone_id]["lambda_rate"] = 1.0 / avg_service

        people_in_queue = sum(
            zone["people_in_queue"] for zone in zone_metrics.values()
        )
        people_in_queue_zones = sum(
            zone["people_detected"] for zone in zone_metrics.values()
        )
        
        return {
            "total_people": total_people,
            "people_in_queue": people_in_queue,
            "people_in_queue_zones": people_in_queue_zones,
            "queue_zones": list(zone_metrics.values()),
            "boxes": metadata_boxes
        }
