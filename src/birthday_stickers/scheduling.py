"""Conservative GPU concurrency policy: different ports do not imply different GPUs."""

import ipaddress
import socket
from urllib.parse import urlsplit


def address_key(value: str) -> str:
    address = ipaddress.ip_address(value.split("%")[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return "loopback" if address.is_loopback else str(address)


def host_addresses(host: str) -> set[str]:
    try:
        return {address_key(host)}
    except ValueError:
        return {
            address_key(item[4][0])
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }


def gpu_policy(config) -> tuple[str, str]:
    if not config.generation.outpainting:
        return "sequential", "Outpainting is disabled"
    if config.generation.gpu_mode == "concurrent":
        return "concurrent", "Alternate image generation and outpainting without GPU overlap"
    if config.generation.gpu_mode == "sequential":
        return "sequential", "Finish all image generation before starting outpainting"
    primary = urlsplit(config.comfy.base_url).hostname.lower().rstrip(".")
    secondary = urlsplit(config.outpainting_comfy.base_url).hostname.lower().rstrip(".")
    if primary == secondary:
        return "sequential", "Generation and outpainting use the same host (ports are ignored)"
    try:
        first, second = host_addresses(primary), host_addresses(secondary)
    except (OSError, ValueError):
        return "sequential", "Could not verify distinct ComfyUI IPs; using sequential GPU work"
    if not first or not second or first & second:
        return "sequential", "Generation and outpainting resolve to the same IP"
    return "parallel", "Generation and outpainting use different ComfyUI IPs"
