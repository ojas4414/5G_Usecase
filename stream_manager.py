import cv2
import queue
import threading
import time
import logging
import os

from network_simulator import net_sim

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(threadName)s: %(message)s")
logger = logging.getLogger(__name__)


def resolve_camera_source(camera_url: str, camera_http_url: str) -> str:
    """
    Resolves the best available video source given the config values.
    Implements the fallback chain:
        RTSP (camera_url) → HTTP MJPEG (camera_http_url)

    Returns the first URL that OpenCV can successfully open and read a frame from.
    Raises RuntimeError if neither works.
    """
    candidates = []
    if camera_url:
        candidates.append(("RTSP", camera_url))
    if camera_http_url:
        candidates.append(("HTTP-MJPEG", camera_http_url))

    if not candidates:
        raise RuntimeError(
            "use_real_camera is True but both camera_url and camera_http_url "
            "are empty in config.yaml. Please set at least one."
        )

    for label, url in candidates:
        logger.info(f"[CameraResolver] Trying {label}: {url}")
        # Quick open test — we attempt a single frame read with a 5s timeout
        import threading as _t
        success = [False]

        def _test(u=url, r=success):
            cap = cv2.VideoCapture(u)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    r[0] = True
            cap.release()

        thread = _t.Thread(target=_test, daemon=True)
        thread.start()
        thread.join(timeout=6.0)

        if success[0]:
            logger.info(f"[CameraResolver] ✓ Using {label}: {url}")
            return url
        else:
            logger.warning(f"[CameraResolver] ✗ {label} failed: {url}")

    raise RuntimeError(
        "Neither RTSP nor HTTP MJPEG could be opened.\n"
        "  • Run: python camera_finder.py   to auto-discover the camera URL\n"
        "  • Check the camera IP is reachable: ping <camera-ip>\n"
        "  • Try opening the camera's web UI in a browser to find the stream path"
    )


class VideoStream:
    """
    Concept: Producer-Consumer Threading & Ring Buffer for Low Latency.

    In streaming (especially over 5G), latency is worse than dropped frames.
    Our 'Producer' grabs frames from the camera as fast as possible.
    If the 'Consumer' (YOLO AI) takes 30ms to process a frame, the camera might
    have captured 2 more. Instead of processing stale frames (which builds latency),
    we overwrite the buffer so the AI ALWAYS gets the freshest frame.

    5G Camera additions:
    - Real inter-frame delivery gap is measured on every read and fed into
      net_sim.update_frame_gap_latency() for probe-less latency inference.
    - last_frame_latency_ms is exposed as a public property for the telemetry
      dashboard to surface real 5G link quality.
    - Auto-reconnect logic handles transient 5G link interruptions gracefully.
    """

    def __init__(self, src=0):
        self.src = src
        self._last_frame_time: float = 0.0
        self._last_frame_latency_ms: float = 0.0

        # ── Source-type detection ──────────────────────────────────────────
        if isinstance(self.src, int) or (isinstance(self.src, str) and str(self.src).isdigit()):
            # Local webcam — use DirectShow to eliminate Windows MSMF buffering
            self.cap = cv2.VideoCapture(int(self.src), cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            logger.info(f"[VideoStream] Local webcam source: {self.src}")

        elif isinstance(self.src, str) and self.src.startswith("rtsp://"):
            # 5G / IP Camera — RTSP over UDP with zero-latency FFMPEG flags
            # nobuffer + low_delay prevents the decoder from queuing frames,
            # ensuring we always decode the most recently received GOP.
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;udp|"
                "fflags;nobuffer|"
                "flags;low_delay|"
                "analyzeduration;0|"
                "probesize;32"           # Minimal probe → faster first-frame
            )
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info(f"[VideoStream] RTSP camera source: {self.src}")

        elif isinstance(self.src, str) and self.src.startswith("http://"):
            # HTTP MJPEG — common on cameras connected directly via Ethernet
            # when RTSP is unavailable or disabled.
            # OpenCV reads MJPEG-over-HTTP natively via FFMPEG.
            # nobuffer ensures we don't accumulate frames in the HTTP receive buffer.
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "fflags;nobuffer|"
                "flags;low_delay|"
                "analyzeduration;0"
            )
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info(f"[VideoStream] HTTP MJPEG camera source: {self.src}")

        else:
            # File / other path (offline testing)
            self.cap = cv2.VideoCapture(self.src)
            logger.info(f"[VideoStream] File source: {self.src}")

        # ── Ring buffer: maxsize=1 ─────────────────────────────────────────
        # A single-slot queue acts as the "latest frame" register.
        # When the queue is full the producer evicts the stale frame before
        # pushing the new one — zero accumulation of backlog.
        self.frame_queue = queue.Queue(maxsize=1)
        self.stopped = False

        self.thread = threading.Thread(
            target=self._update, daemon=True, name="CameraIngestThread"
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> "VideoStream":
        """Starts the producer thread."""
        self.thread.start()
        return self

    def read(self) -> "cv2.Mat | None":
        """
        Consumer method: blocks until a frame is available and returns it.
        Also measures the real inter-frame delivery gap and exposes it as
        last_frame_latency_ms for dashboard telemetry.
        """
        frame = self.frame_queue.get()

        now = time.time()
        if self._last_frame_time > 0:
            gap_ms = (now - self._last_frame_time) * 1000.0
            self._last_frame_latency_ms = gap_ms
            # Feed gap into net_sim so processor adaptive logic works without a probe
            net_sim.update_frame_gap_latency(gap_ms)
        self._last_frame_time = now

        return frame

    @property
    def last_frame_latency_ms(self) -> float:
        """Real measured inter-frame delivery gap in milliseconds."""
        return self._last_frame_latency_ms

    def stop(self) -> None:
        """Gracefully release hardware resources."""
        self.stopped = True
        self.thread.join(timeout=3.0)
        self.cap.release()

    # ── Producer thread ────────────────────────────────────────────────────

    def _update(self) -> None:
        """
        Runs continuously in the background thread.
        Reads frames from the OS / network stack and pushes them into the ring buffer.
        Handles 5G link interruptions via exponential-backoff reconnection.
        """
        reconnect_delay = 2.0   # seconds — doubles on each consecutive failure
        MAX_RECONNECT_DELAY = 30.0

        while not self.stopped:
            if not self.cap.isOpened():
                logger.warning(
                    f"[VideoStream] Source unavailable. Reconnecting in {reconnect_delay:.1f}s..."
                )
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
                self._open_cap()
                continue

            ret, frame = self.cap.read()

            if not ret:
                logger.error("[VideoStream] cap.read() failed — possible 5G packet loss or link drop.")
                time.sleep(0.05)    # Brief pause; don't spin on a broken link
                continue

            # Successful read → reset backoff
            reconnect_delay = 2.0

            # ── Ring-buffer swap ───────────────────────────────────────────
            # Evict the stale unread frame to make room for the freshest one.
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass

            self.frame_queue.put(frame)

    def _open_cap(self) -> None:
        """Re-opens the capture device using the same source-type logic as __init__."""
        self.cap.release()
        if isinstance(self.src, int) or (isinstance(self.src, str) and str(self.src).isdigit()):
            self.cap = cv2.VideoCapture(int(self.src), cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        elif isinstance(self.src, str) and (self.src.startswith("rtsp://") or self.src.startswith("http://")):
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self.cap = cv2.VideoCapture(self.src)
