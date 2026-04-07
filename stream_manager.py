import cv2
import queue
import threading
import time
import logging

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(threadName)s: %(message)s")

class VideoStream:
    """
    Concept: Producer-Consumer Threading & Ring Buffer for Low Latency.
    In streaming (especially over 5G), Latency is worse than Dropped Frames.
    Our 'Producer' grabs frames from the camera as fast as possible.
    If the 'Consumer' (YOLO AI) takes 30ms to process a frame, the camera might have captured 2 more frames.
    Instead of processing old frames (which builds latency), we overwrite the buffer so the AI ALWAYS gets the freshest frame.
    """
    def __init__(self, src=0):
        self.src = src
        
        # Concept: Hardware-level Latency Reduction
        # Windows default webcam driver (MSMF) buffers up to 5-10 frames intrinsically.
        # DirectShow (DSHOW) with a forced BufferSize of 1 eliminates hardware lag before it even hits our Python thread.
        # If it's a URL or path, default back to standard capture.
        if isinstance(self.src, int) or str(self.src).isdigit():
            self.cap = cv2.VideoCapture(int(self.src), cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
        elif isinstance(self.src, str) and "://" in self.src:
            # 5G Protocol Optimization: Hardware IP cameras buffer aggressively by default.
            # We strictly enforce zero-latency flags via OpenCV's underlying FFMPEG daemon.
            import os
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|fflags;nobuffer|flags;low_delay"
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self.cap = cv2.VideoCapture(self.src)
        
        # We use a Queue with a maxsize of 1. 
        # This acts as a single-item buffer.
        self.frame_queue = queue.Queue(maxsize=1)
        self.stopped = False
        
        # We spawn a Daemon thread. Daemons automatically die if the main Python script dies.
        self.thread = threading.Thread(target=self._update, args=(), daemon=True, name="CameraIngestThread")

    def start(self):
        """Starts the producer thread."""
        self.thread.start()
        return self

    def _update(self):
        """
        Runs continuously in the background thread.
        Reads frames from the OS and puts them in our Queue.
        """
        while not self.stopped:
            if not self.cap.isOpened():
                logging.warning("Camera disconnected! Attempting reconnect in 2s...")
                time.sleep(2.0)
                if isinstance(self.src, int) or str(self.src).isdigit():
                    self.cap.open(int(self.src), cv2.CAP_DSHOW)
                elif isinstance(self.src, str) and "://" in self.src:
                    self.cap.open(self.src, cv2.CAP_FFMPEG)
                else:
                    self.cap.open(self.src)
                continue

            ret, frame = self.cap.read()
            if not ret:
                logging.error("Failed to read frame. Retrying...")
                time.sleep(0.1)
                continue
            
            # If the queue is full, the AI hasn't grabbed the last frame yet.
            # We purposely discard the old unread frame to make room for `frame` (the newest one).
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            
            # Push the newest frame into the queue
            self.frame_queue.put(frame)

    def read(self):
        """
        Consumer method: Used by the AI/Flask to grab the latest frame.
        Blocks until a frame is available.
        """
        return self.frame_queue.get()

    def stop(self):
        """Gracefully release hardware resources."""
        self.stopped = True
        self.thread.join()
        self.cap.release()
