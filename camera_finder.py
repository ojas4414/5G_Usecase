"""
camera_finder.py
─────────────────────────────────────────────────────────────────────────────
Standalone utility: scans your Ethernet interface for an IP camera and
tests every common stream protocol automatically.

Run with:
    python camera_finder.py
    python camera_finder.py --subnet 169.254.0.0/16   # link-local fallback
    python camera_finder.py --ip 192.168.1.50          # skip scan, test one IP

It will print the exact camera_url / camera_http_url value to paste into
config.yaml.
─────────────────────────────────────────────────────────────────────────────
"""

import socket
import subprocess
import platform
import sys
import time
import argparse
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2

# ── Common camera stream paths to probe ──────────────────────────────────────
RTSP_PATHS = [
    "rtsp://{ip}:554/stream1",
    "rtsp://{ip}:554/live",
    "rtsp://{ip}:554/h264",
    "rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0",
    "rtsp://{ip}:8554/stream1",
    "rtsp://{ip}:8554/live",
    "rtsp://{ip}/stream1",
]

MJPEG_PATHS = [
    "http://{ip}/video",
    "http://{ip}/mjpeg",
    "http://{ip}/mjpg/video.mjpg",
    "http://{ip}:80/video",
    "http://{ip}:8080/video",
    "http://{ip}:8080/mjpeg",
    "http://{ip}/cgi-bin/mjpg/video.cgi",
    "http://{ip}/videostream.cgi",
    "http://{ip}/axis-cgi/mjpg/video.cgi",
]

HTTP_JPEG_PATHS = [
    "http://{ip}/snapshot.jpg",
    "http://{ip}/image.jpg",
    "http://{ip}/cgi-bin/camera",
]

# Ports that indicate a camera is likely present
SCAN_PORTS = [80, 554, 8080, 8554, 8000, 443]


# ─────────────────────────────────────────────────────────────────────────────
# Network helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_local_subnets() -> list[str]:
    """Returns subnets inferred from all non-loopback IPv4 interfaces."""
    subnets = []
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    try:
                        net = ipaddress.IPv4Network(
                            f"{addr.address}/{addr.netmask}", strict=False
                        )
                        subnets.append(str(net))
                    except Exception:
                        pass
    except ImportError:
        # Fallback: hostname-based guess
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        base = ".".join(local_ip.split(".")[:3])
        subnets.append(f"{base}.0/24")
    return subnets


def is_port_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Returns True if the given TCP port is open on ip."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def scan_subnet(subnet: str, max_workers: int = 128) -> list[str]:
    """Scans a subnet for hosts with any camera-relevant port open."""
    print(f"\n[Scan] Probing subnet {subnet} (this takes ~10s)...")
    net = ipaddress.IPv4Network(subnet, strict=False)
    hosts = list(net.hosts())

    found = []

    def check_host(ip_obj):
        ip = str(ip_obj)
        for port in SCAN_PORTS:
            if is_port_open(ip, port, timeout=0.4):
                return ip
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(check_host, ip): ip for ip in hosts}
        for future in as_completed(futures):
            result = future.result()
            if result:
                found.append(result)
                print(f"  ✓ Found device: {result}")

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Stream probe helpers
# ─────────────────────────────────────────────────────────────────────────────

def _try_opencv_url(url: str, timeout_s: float = 3.0, label: str = "") -> bool:
    """
    Tries to open url with OpenCV and read one frame.
    Returns True if a valid frame was received within timeout_s.
    """
    import threading

    result = [False]

    def attempt():
        cap = cv2.VideoCapture(url)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                result[0] = True
        cap.release()

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    return result[0]


def probe_camera(ip: str) -> dict:
    """
    Tests all known stream protocols/paths for a given IP.
    Returns a dict with keys 'rtsp', 'mjpeg', 'jpeg' listing working URLs.
    """
    print(f"\n[Probe] Testing {ip}...")
    results = {"rtsp": [], "mjpeg": [], "jpeg": []}

    # RTSP
    for tmpl in RTSP_PATHS:
        url = tmpl.format(ip=ip)
        sys.stdout.write(f"  RTSP  {url} ... ")
        sys.stdout.flush()
        ok = _try_opencv_url(url, timeout_s=4.0)
        print("✓ WORKS" if ok else "✗")
        if ok:
            results["rtsp"].append(url)

    # HTTP MJPEG
    for tmpl in MJPEG_PATHS:
        url = tmpl.format(ip=ip)
        sys.stdout.write(f"  MJPEG {url} ... ")
        sys.stdout.flush()
        ok = _try_opencv_url(url, timeout_s=4.0)
        print("✓ WORKS" if ok else "✗")
        if ok:
            results["mjpeg"].append(url)

    # HTTP JPEG (snapshot polling)
    for tmpl in HTTP_JPEG_PATHS:
        url = tmpl.format(ip=ip)
        sys.stdout.write(f"  JPEG  {url} ... ")
        sys.stdout.flush()
        ok = _try_opencv_url(url, timeout_s=3.0)
        print("✓ WORKS" if ok else "✗")
        if ok:
            results["jpeg"].append(url)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def print_report(ip: str, probe_results: dict):
    any_found = any(probe_results.values())
    print("\n" + "═" * 60)
    if not any_found:
        print(f"  ✗  No working stream found on {ip}")
        print("  Try:")
        print("    • Opening http://<camera-ip> in a browser to find the stream path")
        print("    • Checking the camera's manual for its default stream URL")
        print("    • Trying with credentials: rtsp://admin:admin@<ip>:554/stream1")
        print("═" * 60)
        return

    print(f"  ✓  Working streams found on {ip}")
    print("═" * 60)

    # Priority: RTSP > MJPEG > JPEG
    best_url = None
    best_key = None
    for key in ("rtsp", "mjpeg", "jpeg"):
        if probe_results[key]:
            best_url = probe_results[key][0]
            best_key = key
            break

    print("\n▶  Paste into config.yaml:\n")
    print("  use_real_camera: true")
    if best_key in ("rtsp",):
        print(f'  camera_url: "{best_url}"')
    else:
        print(f'  camera_url: ""                  # RTSP not working')
        print(f'  camera_http_url: "{best_url}"   # ← use this instead')
    print(f'  real_latency_probe_host: "{ip}"')

    print("\n▶  All working URLs:\n")
    for key, urls in probe_results.items():
        for u in urls:
            print(f"  [{key.upper():5}] {u}")
    print("═" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Auto-discover an IP camera connected via Ethernet."
    )
    parser.add_argument("--ip",     help="Skip scan, test this specific IP directly")
    parser.add_argument("--subnet", help="Override subnet to scan (CIDR, e.g. 192.168.1.0/24)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════════╗")
    print("║   5G Edge Analytics — Camera Finder Utility     ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # ── Mode 1: user provided a specific IP ───────────────────────────────
    if args.ip:
        results = probe_camera(args.ip)
        print_report(args.ip, results)
        return

    # ── Mode 2: scan subnet ───────────────────────────────────────────────
    if args.subnet:
        subnets = [args.subnet]
    else:
        subnets = get_local_subnets()
        # Also add link-local range for cameras with auto-IP (169.254.x.x)
        if not any("169.254" in s for s in subnets):
            subnets.append("169.254.0.0/16")

    print(f"[Info] Scanning subnets: {', '.join(subnets)}")

    all_candidates = []
    for subnet in subnets:
        # Skip huge subnets to avoid a 10-minute scan
        net = ipaddress.IPv4Network(subnet, strict=False)
        if net.num_addresses > 512:
            print(f"[Skip] {subnet} is too large (>{net.num_addresses} hosts). "
                  f"Use --ip <camera-ip> to test directly.")
            continue
        all_candidates += scan_subnet(subnet)

    if not all_candidates:
        print("\n[!] No devices found with camera-relevant ports open.")
        print("    • Make sure the camera is powered and the Ethernet cable is plugged in.")
        print("    • Check Windows IP settings: both laptop and camera must be on the same subnet.")
        print("    • Try: python camera_finder.py --ip <camera-ip>")
        return

    print(f"\n[Info] Found {len(all_candidates)} candidate device(s). Probing streams...\n")
    for ip in all_candidates:
        results = probe_camera(ip)
        print_report(ip, results)


if __name__ == "__main__":
    main()
