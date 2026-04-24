from __future__ import annotations

import logging
import os
import queue
import threading
import time
from urllib.parse import urlsplit

import cv2

from camera_finder import (
    build_preferred_urls,
    discover_camera,
    normalize_rtsp_transport_order,
)
from network_simulator import net_sim

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(threadName)s: %(message)s")
logger = logging.getLogger(__name__)


def _ffmpeg_options_for_transport(transport: str) -> str:
    return (
        f"rtsp_transport;{transport}|"
        "fflags;nobuffer|"
        "flags;low_delay|"
        "analyzeduration;0|"
        "probesize;32"
    )


def _try_video_source(
    url: str,
    timeout_s: float = 6.0,
    rtsp_transport_order: tuple[str, ...] = ("tcp", "udp"),
) -> bool:
    """Attempts to open a URL and read a single frame."""
    transport_attempts = [None]
    if url.startswith("rtsp://"):
        transport_attempts = normalize_rtsp_transport_order(rtsp_transport_order)

    for transport in transport_attempts:
        success = [False]
        previous_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

        def _test() -> None:
            if transport:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _ffmpeg_options_for_transport(
                    transport
                )
                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(url)

            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    success[0] = True
            cap.release()

        thread = threading.Thread(target=_test, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)

        if previous_options is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = previous_options

        if success[0]:
            return True

    return False


def _extract_camera_host(url: str) -> str | None:
    return urlsplit(url).hostname


def resolve_camera_source(
    camera_url: str,
    camera_http_url: str,
    auto_discovery: dict | None = None,
) -> tuple[str, str | None]:
    """
    Resolves the best available real-camera source.

    Order:
        1. Configured RTSP/HTTP URLs
        2. Automatic discovery on reachable subnets
        3. Configured URLs again if discovery was preferred first

    Returns:
        (video_source_url, camera_host_for_probe)
    """
    auto_discovery = auto_discovery or {}
    auto_enabled = auto_discovery.get("enabled", True)
    prefer_auto_discovery = auto_discovery.get("prefer_scanned_camera", False)
    rtsp_transport_order = tuple(
        normalize_rtsp_transport_order(auto_discovery.get("rtsp_transport_order"))
    )

    configured_candidates = []
    if camera_url:
        configured_candidates.append(("RTSP", camera_url))
    if camera_http_url:
        configured_candidates.append(("HTTP-MJPEG", camera_http_url))

    def try_configured_candidates() -> tuple[str, str | None] | None:
        for label, url in configured_candidates:
            logger.info(f"[CameraResolver] Trying {label}: {url}")
            if _try_video_source(url, rtsp_transport_order=rtsp_transport_order):
                logger.info(f"[CameraResolver] Using {label}: {url}")
                return url, _extract_camera_host(url)
            logger.warning(f"[CameraResolver] {label} failed: {url}")
        return None

    if not prefer_auto_discovery:
        configured_match = try_configured_candidates()
        if configured_match:
            return configured_match

    if auto_enabled:
        logger.info("[CameraResolver] Trying automatic camera discovery...")
        discovery = discover_camera(
            candidate_ips=auto_discovery.get("candidate_ips"),
            subnets=auto_discovery.get("subnets"),
            preferred_urls=build_preferred_urls(
                camera_url=camera_url,
                camera_http_url=camera_http_url,
            ),
            rtsp_transport_order=rtsp_transport_order,
            include_local_subnets=auto_discovery.get("include_local_subnets", True),
            max_hosts_per_subnet=int(auto_discovery.get("max_hosts_per_subnet", 512)),
            max_workers=int(auto_discovery.get("max_workers", 128)),
            verbose=bool(auto_discovery.get("verbose", True)),
        )
        if discovery:
            discovered_kind = str(discovery["kind"]).upper()
            discovered_url = str(discovery["url"])
            discovered_ip = str(discovery["ip"])
            logger.info(
                "[CameraResolver] Using auto-discovered %s stream: %s",
                discovered_kind,
                discovered_url,
            )
            return discovered_url, discovered_ip

    if prefer_auto_discovery:
        configured_match = try_configured_candidates()
        if configured_match:
            return configured_match

    raise RuntimeError(
        "No camera stream could be opened.\n"
        "  - Checked configured RTSP/HTTP URLs from config.yaml\n"
        "  - Tried automatic discovery on the current network\n"
        "  - If your lab network is not the VM's local subnet, add it under\n"
        "    camera_auto_discovery.subnets in config.yaml"
    )


class VideoStream:
    """
    Producer-consumer video ingest with a ring buffer.

    The queue has maxsize=1 so inference always sees the latest frame instead of
    building up latency behind the live feed.
    """

    def __init__(self, src=0, rtsp_transport: str = "tcp"):
        self.src = src
        self.rtsp_transport = normalize_rtsp_transport_order((rtsp_transport,))[0]
        self._last_frame_time: float = 0.0
        self._last_frame_latency_ms: float = 0.0

        if isinstance(self.src, int) or (isinstance(self.src, str) and str(self.src).isdigit()):
            self.cap = cv2.VideoCapture(int(self.src), cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            logger.info(f"[VideoStream] Local webcam source: {self.src}")
        elif isinstance(self.src, str) and self.src.startswith("rtsp://"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _ffmpeg_options_for_transport(
                self.rtsp_transport
            )
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info(
                f"[VideoStream] RTSP camera source ({self.rtsp_transport}): {self.src}"
            )
        elif isinstance(self.src, str) and self.src.startswith("http://"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "fflags;nobuffer|"
                "flags;low_delay|"
                "analyzeduration;0"
            )
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info(f"[VideoStream] HTTP MJPEG camera source: {self.src}")
        else:
            self.cap = cv2.VideoCapture(self.src)
            logger.info(f"[VideoStream] File source: {self.src}")

        self.frame_queue = queue.Queue(maxsize=1)
        self.stopped = False
        self.thread = threading.Thread(
            target=self._update,
            daemon=True,
            name="CameraIngestThread",
        )

    def start(self) -> "VideoStream":
        """Starts the producer thread."""
        self.thread.start()
        return self

    def read(self) -> "cv2.Mat | None":
        """
        Returns the freshest available frame and records real frame-gap latency.
        """
        frame = self.frame_queue.get()

        now = time.time()
        if self._last_frame_time > 0:
            gap_ms = (now - self._last_frame_time) * 1000.0
            self._last_frame_latency_ms = gap_ms
            net_sim.update_frame_gap_latency(gap_ms)
        self._last_frame_time = now

        return frame

    @property
    def last_frame_latency_ms(self) -> float:
        """Real measured inter-frame delivery gap in milliseconds."""
        return self._last_frame_latency_ms

    def stop(self) -> None:
        """Gracefully releases the capture device."""
        self.stopped = True
        self.thread.join(timeout=3.0)
        self.cap.release()

    def _update(self) -> None:
        """
        Continuously pulls frames from the OS/network stack into the ring buffer.
        """
        reconnect_delay = 2.0
        max_reconnect_delay = 30.0

        while not self.stopped:
            if not self.cap.isOpened():
                logger.warning(
                    f"[VideoStream] Source unavailable. Reconnecting in {reconnect_delay:.1f}s..."
                )
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                self._open_cap()
                continue

            ret, frame = self.cap.read()
            if not ret:
                logger.error(
                    "[VideoStream] cap.read() failed - possible packet loss or link drop."
                )
                time.sleep(0.05)
                continue

            reconnect_delay = 2.0

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
        elif isinstance(self.src, str) and (
            self.src.startswith("rtsp://") or self.src.startswith("http://")
        ):
            if self.src.startswith("rtsp://"):
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _ffmpeg_options_for_transport(
                    self.rtsp_transport
                )
            self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self.cap = cv2.VideoCapture(self.src)
