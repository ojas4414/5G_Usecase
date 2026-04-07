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
    queue_predictor = StatisticalQueuePredictor()
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
        density = calculate_density(logic_data["total_people"], config.get("roi_area_sqm", 10.0))
        wait_sec = queue_predictor.predict_wait(
            logic_data["total_people"], 
            logic_data["people_in_queue"], 
            logic_data.get("lambda_rate", 0.0)
        )
        
        # 3. Payload Serialization Simulation (Phase 3)
        # Struct binary format bytes
        binary_payload = struct.pack('!2i2f', 
            logic_data["total_people"], 
            logic_data["people_in_queue"], 
            density, 
            wait_sec
        )
        payload_size_bytes = len(binary_payload)
        
        metrics_log.append({
            "Frame": frames_processed,
            "Total_People": logic_data["total_people"],
            "Queued_People": logic_data["people_in_queue"],
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
