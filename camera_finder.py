"""
camera_finder.py
----------------
Reusable camera discovery helpers plus a CLI utility.

Run with:
    python camera_finder.py
    python camera_finder.py --subnet 10.101.0.0/24
    python camera_finder.py --ip 10.101.0.17

The helpers are also used by app startup so real-camera mode can auto-discover
the current camera IP when a previously saved address is stale.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import cv2

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

SCAN_PORTS = [80, 554, 8080, 8554, 8000, 443]


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _log(message: str, verbose: bool) -> None:
    if verbose:
        print(message)


def get_local_subnets(max_hosts_per_subnet: int | None = 512) -> list[str]:
    """
    Returns local IPv4 subnets for all non-loopback interfaces.

    Large networks are reduced to the interface's /24 segment so auto-discovery
    stays fast enough to use at app startup.
    """
    subnets: list[str] = []

    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family != socket.AF_INET or addr.address.startswith("127."):
                    continue
                try:
                    network = ipaddress.IPv4Network(
                        f"{addr.address}/{addr.netmask}",
                        strict=False,
                    )
                except Exception:
                    continue

                if network.num_addresses <= 1:
                    continue

                if max_hosts_per_subnet and network.num_addresses > max_hosts_per_subnet:
                    network = ipaddress.IPv4Network(f"{addr.address}/24", strict=False)
                subnets.append(str(network))
    except ImportError:
        for family, _, _, _, sockaddr in socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
        ):
            if family != socket.AF_INET:
                continue
            ip = sockaddr[0]
            if ip.startswith("127."):
                continue
            subnets.append(str(ipaddress.IPv4Network(f"{ip}/24", strict=False)))

    return _dedupe(subnets)


def normalize_rtsp_transport_order(
    transport_order: Iterable[str] | None = None,
) -> list[str]:
    allowed = {"tcp", "udp"}
    normalized = []
    for transport in transport_order or ("tcp", "udp"):
        value = str(transport).strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    if not normalized:
        normalized = ["tcp", "udp"]
    return normalized


def _ffmpeg_options_for_transport(transport: str) -> str:
    return (
        f"rtsp_transport;{transport}|"
        "fflags;nobuffer|"
        "flags;low_delay|"
        "analyzeduration;0|"
        "probesize;32"
    )


def is_port_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Returns True if the given TCP port is open on ip."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def scan_subnet(
    subnet: str,
    max_workers: int = 128,
    verbose: bool = True,
) -> list[str]:
    """Scans a subnet for hosts with any camera-relevant port open."""
    _log(f"\n[Scan] Probing subnet {subnet}...", verbose)
    network = ipaddress.IPv4Network(subnet, strict=False)

    found: list[str] = []

    def check_host(ip_obj: ipaddress.IPv4Address) -> str | None:
        ip = str(ip_obj)
        for port in SCAN_PORTS:
            if is_port_open(ip, port, timeout=0.4):
                return ip
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_host, ip): ip
            for ip in network.hosts()
        }
        for future in as_completed(futures):
            result = future.result()
            if not result or result in found:
                continue
            found.append(result)
            _log(f"  + Found device: {result}", verbose)

    return found


def swap_ip_in_url(url: str, ip: str) -> str | None:
    """Rebuilds a URL with a new host while preserving credentials/path."""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.hostname:
        return None

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password is not None:
            auth += f":{parsed.password}"
        auth += "@"

    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{auth}{ip}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def build_preferred_urls(
    camera_url: str = "",
    camera_http_url: str = "",
) -> dict[str, list[str]]:
    preferred = {"rtsp": [], "mjpeg": [], "jpeg": []}

    if camera_url:
        preferred["rtsp"].append(camera_url)

    if camera_http_url:
        path = urlsplit(camera_http_url).path.lower()
        if path.endswith((".jpg", ".jpeg")):
            preferred["jpeg"].append(camera_http_url)
        else:
            preferred["mjpeg"].append(camera_http_url)

    return preferred


def _build_probe_plan(
    ip: str,
    preferred_urls: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    plan = {"rtsp": [], "mjpeg": [], "jpeg": []}

    for kind in plan:
        raw_urls = (preferred_urls or {}).get(kind, [])
        plan[kind].extend(
            swapped
            for raw_url in raw_urls
            if (swapped := swap_ip_in_url(raw_url, ip))
        )

    plan["rtsp"].extend(tmpl.format(ip=ip) for tmpl in RTSP_PATHS)
    plan["mjpeg"].extend(tmpl.format(ip=ip) for tmpl in MJPEG_PATHS)
    plan["jpeg"].extend(tmpl.format(ip=ip) for tmpl in HTTP_JPEG_PATHS)

    for kind, urls in plan.items():
        plan[kind] = _dedupe(urls)

    return plan


def _try_opencv_url(
    url: str,
    timeout_s: float = 3.0,
    rtsp_transport_order: Iterable[str] | None = None,
) -> bool:
    """
    Tries to open url with OpenCV and read one frame.
    Returns True if a valid frame was received within timeout_s.
    """
    import threading

    transport_attempts = [None]
    if url.startswith("rtsp://"):
        transport_attempts = normalize_rtsp_transport_order(rtsp_transport_order)

    for transport in transport_attempts:
        result = [False]
        previous_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

        def attempt() -> None:
            if transport:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _ffmpeg_options_for_transport(
                    transport
                )
                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(url)

            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None and frame.size > 0:
                    result[0] = True
            cap.release()

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)

        if previous_options is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = previous_options

        if result[0]:
            return True

    return False


def probe_camera(
    ip: str,
    preferred_urls: dict[str, list[str]] | None = None,
    rtsp_transport_order: Iterable[str] | None = None,
    verbose: bool = True,
) -> dict[str, list[str]]:
    """
    Tests common stream protocols/paths for a given IP.
    Returns a dict with keys 'rtsp', 'mjpeg', and 'jpeg'.
    """
    _log(f"\n[Probe] Testing {ip}...", verbose)
    results = {"rtsp": [], "mjpeg": [], "jpeg": []}
    plan = _build_probe_plan(ip, preferred_urls=preferred_urls)

    for label, key, timeout_s in (
        ("RTSP", "rtsp", 4.0),
        ("MJPEG", "mjpeg", 4.0),
        ("JPEG", "jpeg", 3.0),
    ):
        for url in plan[key]:
            if verbose:
                sys.stdout.write(f"  {label:<5} {url} ... ")
                sys.stdout.flush()
            ok = _try_opencv_url(
                url,
                timeout_s=timeout_s,
                rtsp_transport_order=rtsp_transport_order,
            )
            if verbose:
                print("WORKS" if ok else "x")
            if ok:
                results[key].append(url)

    return results


def pick_best_stream(probe_results: dict[str, list[str]]) -> tuple[str | None, str | None]:
    for key in ("rtsp", "mjpeg", "jpeg"):
        urls = probe_results.get(key, [])
        if urls:
            return key, urls[0]
    return None, None


def discover_camera(
    candidate_ips: Iterable[str] | None = None,
    subnets: Iterable[str] | None = None,
    preferred_urls: dict[str, list[str]] | None = None,
    rtsp_transport_order: Iterable[str] | None = None,
    include_local_subnets: bool = True,
    max_hosts_per_subnet: int = 512,
    max_workers: int = 128,
    verbose: bool = True,
) -> dict[str, object] | None:
    """
    Finds the first reachable camera stream.

    Returns:
        {
            "ip": "10.101.0.17",
            "kind": "rtsp",
            "url": "rtsp://user:pass@10.101.0.17/...",
            "results": {...}
        }
    """
    candidate_ip_list = _dedupe(candidate_ips or [])
    subnet_list = _dedupe(subnets or [])

    if include_local_subnets:
        subnet_list = _dedupe(
            list(subnet_list) + get_local_subnets(max_hosts_per_subnet=max_hosts_per_subnet)
        )

    for subnet in subnet_list:
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError:
            _log(f"[Skip] Invalid subnet '{subnet}'", verbose)
            continue

        if network.num_addresses > max_hosts_per_subnet:
            _log(
                f"[Skip] {subnet} has {network.num_addresses} addresses; "
                f"raise max_hosts_per_subnet to scan it fully.",
                verbose,
            )
            continue

        candidate_ip_list.extend(
            scan_subnet(subnet, max_workers=max_workers, verbose=verbose)
        )
        candidate_ip_list = _dedupe(candidate_ip_list)

    if not candidate_ip_list:
        _log("[Info] No candidate camera IPs found.", verbose)
        return None

    _log(
        f"\n[Info] Probing {len(candidate_ip_list)} candidate device(s)...",
        verbose,
    )
    for ip in candidate_ip_list:
        results = probe_camera(
            ip,
            preferred_urls=preferred_urls,
            rtsp_transport_order=rtsp_transport_order,
            verbose=verbose,
        )
        kind, url = pick_best_stream(results)
        if url:
            return {
                "ip": ip,
                "kind": kind,
                "url": url,
                "results": results,
            }

    return None


def print_report(ip: str, probe_results: dict[str, list[str]]) -> None:
    best_key, best_url = pick_best_stream(probe_results)
    print("\n" + "=" * 60)

    if not best_url:
        print(f"  x No working stream found on {ip}")
        print("  Try:")
        print("    - Open http://<camera-ip> in a browser to inspect the web UI")
        print("    - Check the camera manual for the default stream URL")
        print("    - Keep credentials in camera_url so startup can reuse them")
        print("=" * 60)
        return

    print(f"  + Working streams found on {ip}")
    print("=" * 60)
    print("\n> Paste into config.yaml:\n")
    print("  use_real_camera: true")
    if best_key == "rtsp":
        print(f'  camera_url: "{best_url}"')
    else:
        print('  camera_url: ""')
        print(f'  camera_http_url: "{best_url}"')
    print(f'  real_latency_probe_host: "{ip}"')

    print("\n> All working URLs:\n")
    for key, urls in probe_results.items():
        for url in urls:
            print(f"  [{key.upper():5}] {url}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-discover an IP camera on the current network."
    )
    parser.add_argument("--ip", help="Skip scan and probe this IP directly")
    parser.add_argument(
        "--subnet",
        help="Subnet to scan (CIDR form, for example 10.101.0.0/24)",
    )
    args = parser.parse_args()

    print("\n============================================================")
    print("   5G Edge Analytics - Camera Finder Utility")
    print("============================================================")

    if args.ip:
        results = probe_camera(args.ip, rtsp_transport_order=("tcp", "udp"), verbose=True)
        print_report(args.ip, results)
        return

    if args.subnet:
        subnets = [args.subnet]
    else:
        subnets = get_local_subnets(max_hosts_per_subnet=512)

    print(f"[Info] Scanning subnets: {', '.join(subnets)}")

    candidates: list[str] = []
    for subnet in subnets:
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError:
            print(f"[Skip] Invalid subnet '{subnet}'")
            continue

        if network.num_addresses > 512:
            print(
                f"[Skip] {subnet} has {network.num_addresses} addresses. "
                "Use --subnet or --ip to narrow the scan."
            )
            continue

        candidates.extend(scan_subnet(subnet, verbose=True))
        candidates = _dedupe(candidates)

    if not candidates:
        print("\n[!] No candidate devices found with camera-relevant ports open.")
        print("    - Make sure the camera is powered and on the same reachable network")
        print("    - Try: python camera_finder.py --subnet 10.101.0.0/24")
        print("    - Try: python camera_finder.py --ip <camera-ip>")
        return

    print(f"\n[Info] Found {len(candidates)} candidate device(s).")
    for ip in candidates:
        results = probe_camera(ip, rtsp_transport_order=("tcp", "udp"), verbose=True)
        print_report(ip, results)


if __name__ == "__main__":
    main()
