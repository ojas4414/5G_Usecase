import random
import time
import threading
import subprocess
import platform
import logging

logger = logging.getLogger(__name__)


class NetworkSimulator:
    """
    Dual-mode 5G Network Monitor.

    SIMULATED MODE (use_real_camera=False):
        Injects artificial latency delays and packet drops to mimic 5G slice
        conditions (URLLC, eMBB, Edge_Failure). Used for demos without live hardware.

    REAL CAMERA MODE (use_real_camera=True):
        Runs a background ICMP probe thread that pings the 5G camera host at a
        configurable interval and writes the real measured RTT into
        `current_latency_ms`. No artificial sleep/drop is applied to the
        video pipeline — the real network already provides those conditions.
        Packet loss is inferred from consecutive failed pings.
    """

    PROFILES = {
        "URLLC":           {"latency_ms": (1,   5),    "drop_prob": 0.001},
        "eMBB_Excellent":  {"latency_ms": (15,  30),   "drop_prob": 0.01},
        "eMBB_Congested":  {"latency_ms": (100, 300),  "drop_prob": 0.15},
        "Edge_Failure":    {"latency_ms": (500, 2000), "drop_prob": 0.40},
    }

    def __init__(self, profile_name: str = "eMBB_Excellent"):
        if profile_name not in self.PROFILES:
            profile_name = "eMBB_Excellent"

        self.profile_name = profile_name
        self.profile = self.PROFILES[profile_name]

        # Shared state — written by probe thread, read by processor.py
        self.current_latency_ms: float = 0.0
        self.current_drop_prob: float = self.profile["drop_prob"]

        # Real-camera mode state
        self._real_camera_mode: bool = False
        self._probe_host: str = ""
        self._probe_interval: float = 1.0
        self._probe_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Consecutive failed pings → inferred packet loss
        self._failed_pings: int = 0
        self._FAIL_WINDOW: int = 5  # sliding window for loss estimation

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def set_profile(self, profile_name: str) -> None:
        """Switch simulated network profile (only effective in simulated mode)."""
        if profile_name in self.PROFILES:
            self.profile_name = profile_name
            self.profile = self.PROFILES[profile_name]
            self.current_drop_prob = self.profile["drop_prob"]
            logger.info(f"[NetSim] Switched to profile: {profile_name}")

    def enable_real_camera_mode(self, probe_host: str, probe_interval_sec: float = 1.0) -> None:
        """
        Activates real-camera mode and starts the background ICMP probe thread.
        Call this once at startup when use_real_camera=True.
        """
        self._real_camera_mode = True
        self._probe_host = probe_host
        self._probe_interval = probe_interval_sec

        if probe_host:
            self._stop_event.clear()
            self._probe_thread = threading.Thread(
                target=self._probe_loop,
                daemon=True,
                name="LatencyProbeThread"
            )
            self._probe_thread.start()
            logger.info(f"[NetSim] Real-camera mode active. Probing '{probe_host}' every {probe_interval_sec}s.")
        else:
            # No host given — infer latency from frame gap timestamps instead
            logger.info("[NetSim] Real-camera mode active (no probe host). "
                        "Latency will be measured from frame delivery gaps.")

    def disable_real_camera_mode(self) -> None:
        """Stops the probe thread and returns to simulated mode."""
        self._real_camera_mode = False
        self._stop_event.set()

    @property
    def is_real_camera_mode(self) -> bool:
        return self._real_camera_mode

    # ─────────────────────────────────────────────────────────────────────────
    # Simulated-mode methods (used by app.py ingest_producer)
    # ─────────────────────────────────────────────────────────────────────────

    def simulate_delay(self) -> float:
        """
        Sleeps the calling thread to simulate 5G network latency.
        ONLY called in simulated mode — app.py guards this with use_real_camera check.
        Returns the applied latency in milliseconds.
        """
        latency = random.randint(*self.profile["latency_ms"])
        self.current_latency_ms = float(latency)
        time.sleep(latency / 1000.0)
        return latency

    def should_drop_packet(self) -> bool:
        """
        Returns True to simulate packet loss.
        ONLY called in simulated mode — app.py guards this with use_real_camera check.
        In real-camera mode, actual packet loss manifests as failed cap.read() calls.
        """
        return random.random() < self.profile["drop_prob"]

    def update_frame_gap_latency(self, gap_ms: float) -> None:
        """
        Called by stream_manager when no probe host is configured.
        Feeds real inter-frame delivery gaps into current_latency_ms so
        processor.py's adaptive resolution logic still fires correctly.
        """
        if self._real_camera_mode and not self._probe_host:
            # EMA smoothing (alpha=0.3) to avoid jitter spikes fooling the threshold
            if self.current_latency_ms == 0.0:
                self.current_latency_ms = gap_ms
            else:
                self.current_latency_ms = 0.3 * gap_ms + 0.7 * self.current_latency_ms

    # ─────────────────────────────────────────────────────────────────────────
    # Internal probe loop (real-camera mode only)
    # ─────────────────────────────────────────────────────────────────────────

    def _probe_loop(self) -> None:
        """
        Background thread: pings the camera host once per interval and writes
        the measured RTT into current_latency_ms.
        Consecutive failures increment _failed_pings which drives current_drop_prob.
        """
        while not self._stop_event.is_set():
            rtt = self._ping_host(self._probe_host)
            if rtt is not None:
                # EMA smoothing to avoid spikes from transient jitter
                if self.current_latency_ms == 0.0:
                    self.current_latency_ms = rtt
                else:
                    self.current_latency_ms = 0.3 * rtt + 0.7 * self.current_latency_ms
                self._failed_pings = max(0, self._failed_pings - 1)
            else:
                self._failed_pings = min(self._failed_pings + 1, self._FAIL_WINDOW)
                # Elevate latency significantly on probe failure (link likely degraded)
                self.current_latency_ms = max(500.0, min(self.current_latency_ms * 1.5, 2000.0))

            # Infer packet-loss probability from failed-ping ratio
            self.current_drop_prob = self._failed_pings / self._FAIL_WINDOW

            logger.debug(
                f"[LatencyProbe] RTT={self.current_latency_ms:.1f}ms  "
                f"loss≈{self.current_drop_prob*100:.0f}%"
            )
            self._stop_event.wait(self._probe_interval)

    @staticmethod
    def _ping_host(host: str) -> float | None:
        """
        Sends one ICMP ping and returns RTT in milliseconds, or None on failure.
        Uses the OS-native ping command (cross-platform: Windows & Linux).
        """
        try:
            is_windows = platform.system().lower() == "windows"
            cmd = (
                ["ping", "-n", "1", "-w", "1000", host]
                if is_windows
                else ["ping", "-c", "1", "-W", "1", host]
            )
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.0
            )
            output = result.stdout

            # Parse RTT from ping output
            if is_windows:
                # Windows: "Average = 12ms"
                for line in output.splitlines():
                    if "Average" in line:
                        parts = line.strip().split("=")
                        rtt_str = parts[-1].strip().replace("ms", "")
                        return float(rtt_str)
            else:
                # Linux/macOS: "rtt min/avg/max/mdev = 1.2/1.5/1.8/0.1 ms"
                for line in output.splitlines():
                    if "rtt" in line or "round-trip" in line:
                        avg = line.split("/")[4]
                        return float(avg)
            return None
        except Exception:
            return None


# Global singleton — imported by processor.py, app.py, stream_manager.py
net_sim = NetworkSimulator("eMBB_Excellent")
