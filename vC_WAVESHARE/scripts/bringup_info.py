#!/usr/bin/env python3
"""Print local bring-up values for the WearabLLM v3 bench workflow."""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path


def candidate_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if not address.startswith("127."):
            addresses.add(address)
    except OSError:
        pass
    finally:
        try:
            probe.close()
        except Exception:
            pass

    return sorted(addresses)


def serial_ports() -> list[Path]:
    patterns = [
        "/dev/tty.usbmodem*",
        "/dev/cu.usbmodem*",
        "/dev/tty.usbserial*",
        "/dev/cu.usbserial*",
    ]
    ports: list[Path] = []
    for pattern in patterns:
        ports.extend(Path("/").glob(pattern.removeprefix("/")))
    return sorted(set(ports))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show bridge URLs, serial ports, and menuconfig values for first flash."
    )
    parser.add_argument("--host", default="", help="Override the detected LAN IP/host.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WEARABLLM_BRIDGE_PORT", "8765")),
        help="Bridge HTTP port.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    addresses = candidate_ipv4_addresses()
    host = args.host or (addresses[0] if addresses else "<computer-lan-ip>")
    base_url = f"http://{host}:{args.port}"
    query_url = f"{base_url}/v1/query"
    tts_url = f"{base_url}/v1/tts"

    print("WearabLLM v3 bring-up values")
    print()
    print("Candidate computer LAN IPs:")
    if addresses:
        for address in addresses:
            print(f"  {address}")
    else:
        print("  none detected; use System Settings or `ipconfig getifaddr en0`")

    print()
    print("Bridge URLs:")
    print(f"  base:  {base_url}")
    print(f"  query: {query_url}")
    print(f"  tts:   {tts_url}")

    print()
    print("Firmware menuconfig values:")
    print("  WearabLLM v3 -> Bridge query URL")
    print(f"    {query_url}")
    print("  WearabLLM v3 -> Bridge TTS URL")
    print(f"    {tts_url}")
    print("  WearabLLM v3 -> Minimum push-to-talk capture ms")
    print("    250")
    print("  WearabLLM v3 -> Maximum push-to-talk capture seconds")
    print("    6")
    print("  WearabLLM v3 -> Push-to-talk active level")
    print("    0")
    print("  WearabLLM v3 -> Push-to-talk GPIO pull mode")
    print("    Internal pull-up")

    print()
    print("Bridge commands:")
    print("  ./scripts/run_bridge_dryrun.sh")
    print(f"  WEARABLLM_BRIDGE_BASE_URL={base_url} ./scripts/bridge_smoke.sh")
    print()
    print("Local firmware config commands:")
    print("  ./scripts/configure_firmware.py --status")
    print("  ./scripts/configure_firmware.py --audio-min-capture-ms 250 --audio-max-seconds 6")
    print("  ./scripts/configure_firmware.py --ptt-gpio 0 --ptt-active-level 0 --ptt-pull up")

    ports = serial_ports()
    print()
    print("Candidate ESP32 serial ports:")
    if ports:
        for port in ports:
            print(f"  {port}")
    else:
        print("  none detected yet")

    print()
    print("Flash command:")
    if ports:
        print(f"  ./scripts/firmware_flash_monitor.sh {ports[0]}")
    else:
        print("  ./scripts/firmware_flash_monitor.sh /dev/tty.usbmodemXXXX")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
