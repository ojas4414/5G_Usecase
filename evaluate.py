import time
import cv2
import pandas as pd
import struct
import argparse
from processor import EdgeProcessor
from analytics import calculate_density, StatisticalQueuePredictor
import yaml

def run_benchmark(max_frames=100, video_path=0):
    """
    Runs the 5G Edge Network Crowd system headlessly to gather empirical data
    suitable for academic plotting and evaluation.
    """
    print(f"[*] Starting Evaluation Mode on {video_path}")
    
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        
    processor = EdgeProcessor()
    queue_predictors = {}
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"[!] Error: Could not open video source {video_path}")
        return
        
    metrics_log = []
    frames_processed = 0
    
    while frames_processed < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
            
        start_t = time.time()
        
        # 1. Edge Inference
        logic_data = processor.process_frame(frame)
        inference_time_ms = (time.time() - start_t) * 1000.0
        
        # 2. Metric Calculation
        total_people_in_queue_zones = 0
        total_people_queued = 0
        total_queue_area_sqm = 0.0
        max_queue_wait_sec = 0.0

        for queue_zone in logic_data.get("queue_zones", []):
            zone_id = queue_zone["id"]
            predictor = queue_predictors.setdefault(zone_id, StatisticalQueuePredictor())
            zone_wait_sec = predictor.predict_wait(
                queue_zone["people_detected"],
                queue_zone["people_in_queue"],
                queue_zone.get("lambda_rate", 0.0)
            )

            total_people_in_queue_zones += queue_zone["people_detected"]
            total_people_queued += queue_zone["people_in_queue"]
            total_queue_area_sqm += queue_zone["area_sqm"]
            max_queue_wait_sec = max(max_queue_wait_sec, zone_wait_sec)

        density = calculate_density(total_people_in_queue_zones, total_queue_area_sqm)
        wait_sec = max_queue_wait_sec
        
        # 3. Payload Serialization Simulation (Phase 3)
        # Struct binary format bytes
        binary_payload = struct.pack('!2i2f', 
            logic_data["total_people"], 
            total_people_queued, 
            density, 
            wait_sec
        )
        payload_size_bytes = len(binary_payload)
        
        metrics_log.append({
            "Frame": frames_processed,
            "Total_People": logic_data["total_people"],
            "Queued_People": total_people_queued,
            "Density": density,
            "Est_Wait_Sec": wait_sec,
            "Inference_Latency_ms": round(inference_time_ms, 2),
            "Payload_Size_Bytes": payload_size_bytes
        })
        
        frames_processed += 1
        if frames_processed % 10 == 0:
            print(f"[*] Processed {frames_processed}/{max_frames} frames...")

    cap.release()
    
    # Export for paper plotting
    if not metrics_log:
        print("\n[!] No frames processed, no benchmark results to save.")
        return

    df = pd.DataFrame(metrics_log)
    output_filename = "benchmark_results.csv"
    df.to_csv(output_filename, index=False)
    
    print(f"\n[+] Benchmark Complete! Data saved to {output_filename}")
    print("\n--- Summary Statistics ---")
    print(df.describe().to_string())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Headless Benchmarking")
    parser.add_argument("--frames", type=int, default=100, help="Number of frames to process")
    parser.add_argument("--source", type=str, default="0", help="Video source (0 for webcam, path for video)")
    
    args = parser.parse_args()
    
    # Try converting source to int if it's the default webcam "0"
    source = int(args.source) if args.source.isdigit() else args.source
    run_benchmark(max_frames=args.frames, video_path=source)
