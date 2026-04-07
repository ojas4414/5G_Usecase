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
        self.roi_coords = config["roi_polygon"]
        self.roi_poly = Polygon(self.roi_coords)
        self.queue_time_threshold = config.get("queue_wait_threshold_sec", 5.0)
        self.track_grace_period_sec = config.get("track_grace_period_sec", 2.0)
        
        # Concept: Tracking State Persistence
        # A dictionary to store the exact timestamp each Person ID entered the ROI.
        # Format: {track_id: timestamp_first_entered_roi}
        self.track_history = defaultdict(lambda: None)
        # Keeps track of the last time we saw this person to prevent YOLO dropouts
        self.last_seen = defaultdict(lambda: time.time())
        
        # Concept: Empirical Service Times (Phase 4)
        self.recent_service_times = []

    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, dict]:
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
        
        people_in_queue = 0
        total_people = 0
        
        metadata_boxes = []

        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.cpu().numpy().astype(int)
            total_people = len(track_ids)
            
            # Temporary list to track who we saw this exact frame
            active_ids_in_roi = set()
            
            for box, track_id in zip(boxes, track_ids):
                x1, y1, x2, y2 = map(int, box)
                
                # Concept: Spatial Geometry (Center Point of Bounding Box)
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                # Check if the person's center is inside the ROI bounds
                # This ensures we only care about people actually in the defined line bounds, 
                # filtering out transient random people walking outside.
                if self.roi_poly.contains(Point(center_x, center_y)):
                    active_ids_in_roi.add(track_id)
                    self.last_seen[track_id] = time.time()
                    
                    # If this is the newly detected person in the ROI, record WHEN they entered
                    if self.track_history[track_id] is None:
                        self.track_history[track_id] = time.time()
                    
                    # Calculate how long they have been standing in the ROI
                    time_in_roi = time.time() - self.track_history[track_id]
                    
                    # If they have been in the ROI longer than the 5-second config threshold...
                    if time_in_roi > self.queue_time_threshold:
                        people_in_queue += 1
                        status = "queued"
                    else:
                        status = "waiting"
                        
                    metadata_boxes.append({
                        "id": int(track_id),
                        "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
                        "cx": int(center_x), "cy": int(center_y),
                        "status": status,
                        "time": int(time_in_roi)
                    })

            # Cleanup tracking state for people who left the ROI
            # (Prevents memory leaks over hundreds of hours on a server)
            for old_id in list(self.track_history.keys()):
                if old_id not in active_ids_in_roi:
                    # Check against the grace period to avoid dropping IDs that YOLO momentarily lost
                    time_since_last_seen = time.time() - self.last_seen[old_id]
                    if time_since_last_seen > self.track_grace_period_sec:
                        # Calculate empirical service time before they leave
                        # The fact that they survived the queue threshold enforces they were standing in line, not just randoms.
                        time_spent = time.time() - self.track_history[old_id]
                        if time_spent > self.queue_time_threshold:
                            self.recent_service_times.append(time_spent)
                            if len(self.recent_service_times) > 20:  # Sliding window of 20
                                self.recent_service_times.pop(0)
                                
                        del self.track_history[old_id]
                        if old_id in self.last_seen:
                            del self.last_seen[old_id]


        
        # Calculate dynamic Lambda service rate (Phase 4)
        lambda_rate = 0.0
        if len(self.recent_service_times) > 0:
            avg_service = sum(self.recent_service_times) / len(self.recent_service_times)
            lambda_rate = 1.0 / avg_service
        
        return {
            "total_people": total_people,
            "people_in_queue": people_in_queue,
            "lambda_rate": lambda_rate,
            "boxes": metadata_boxes
        }
