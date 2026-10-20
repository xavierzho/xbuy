from __future__ import annotations

import copy
import random
from typing import Any

from bs_dvid.constants import (
    APP_SIGNATURE,
    DEVICE_CONFIGS,
    DNS_SERVERS,
    GATEWAY_IPS,
    IME_SERVICES,
    NORMAL_STACK_TRACE,
)
from bs_dvid.crypto_util import md5_hash


def random_choice(arr):
    return random.choice(arr)


def random_int(min_v: int, max_v: int) -> int:
    return random.randint(min_v, max_v)


def calculate_luhn_check_digit(digits: str) -> str:
    total = 0
    n = len(digits)
    for i, ch in enumerate(digits):
        digit = int(ch, 10)
        if (n - i) % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - (total % 10)) % 10)


def generate_imei() -> str:
    imei = "35"
    for _ in range(12):
        imei += str(random_int(0, 9))
    imei += calculate_luhn_check_digit(imei)
    return imei


def get_random_device_config() -> dict:
    return copy.deepcopy(random_choice(DEVICE_CONFIGS))


def device_config_by_index(index: int) -> dict:
    return copy.deepcopy(DEVICE_CONFIGS[int(index) % len(DEVICE_CONFIGS)])


def generate_device_fingerprint_from_config(
    device_config: dict,
    *,
    gateway_ip: str | None = None,
    rng: Any | None = None,
    overrides: dict | None = None,
) -> dict:
    """由固定 device_config 构造指纹（parity / 与 flow 共用同一 config）。"""
    overrides = overrides or {}
    config = copy.deepcopy(device_config)
    hash1, hash2 = generate_certificate_hashes(bool(overrides.get("include_system_certs")))
    pick = rng.choice if rng is not None else random_choice
    dns = overrides.get("dns") or pick(DNS_SERVERS)
    network_config = {"1": generate_hosts_content(), "2": dns}
    gw = gateway_ip or overrides.get("gateway_ip") or pick(GATEWAY_IPS)
    battery = overrides.get("battery_level") or f"{random_int(20, 100) if rng is None else rng.randint(20, 100)}%"
    sensors = overrides.get("sensor_count") or str(random_int(2, 4) if rng is None else rng.randint(2, 4))
    ime = overrides.get("ime_service") or pick(IME_SERVICES)
    return {
        "device_config": config,
        "hardware_info": generate_device_hardware_info(config),
        "build_fingerprint": generate_build_fingerprint(config),
        "network_config": network_config,
        "certificate_hash_91": hash1,
        "certificate_hash_92": hash2,
        "ime_service": ime,
        "detection_results": generate_detection_results(),
        "stack_trace": NORMAL_STACK_TRACE,
        "battery_level": battery,
        "sensor_count": sensors,
        "gateway_ip": gw,
    }


def generate_build_fingerprint(config: dict) -> str:
    return (
        f"{config['manufacturer']}/{config['device']}/{config['codename']}:"
        f"{config['android_version']}/{config['build_id']}/{config['build_tags']}"
    )


def generate_device_hardware_info(config: dict) -> dict:
    return {
        "0": config["cpu_arch"],
        "2": config["sdk_version"],
        "3": config["build_id"],
        "4": config["device"],
        "6": config["chipset"],
        "10": config["firmware"],
        "11": config["chip_vendor"],
    }


def generate_hosts_content() -> str:
    return "127.0.0.1       localhost;::1             ip6-localhost"


def generate_network_config() -> dict:
    return {"1": generate_hosts_content(), "2": random_choice(DNS_SERVERS)}


def generate_certificate_hashes(include_system_certs: bool = False) -> tuple[list[str], list[str]]:
    app_cert_hash = md5_hash(APP_SIGNATURE)
    hash1 = [app_cert_hash]
    hash2 = (
        [app_cert_hash, "f0eddd1d306b6e402b0fe5b49e4e4017"]
        if include_system_certs
        else [app_cert_hash]
    )
    return hash1, hash2


def generate_detection_results() -> dict:
    return {
        "1": "-1:oops*abort",
        "2": "-1:tracer:*c",
        "12": {str(i): "0" for i in range(16)},
    }


def generate_gateway_ip() -> str:
    return random_choice(GATEWAY_IPS)


def generate_device_fingerprint(include_system_certs: bool = False) -> dict:
    config = get_random_device_config()
    hash1, hash2 = generate_certificate_hashes(include_system_certs)
    return {
        "device_config": config,
        "hardware_info": generate_device_hardware_info(config),
        "build_fingerprint": generate_build_fingerprint(config),
        "network_config": generate_network_config(),
        "certificate_hash_91": hash1,
        "certificate_hash_92": hash2,
        "ime_service": random_choice(IME_SERVICES),
        "detection_results": generate_detection_results(),
        "stack_trace": NORMAL_STACK_TRACE,
        "battery_level": f"{random_int(20, 100)}%",
        "sensor_count": str(random_int(2, 4)),
    }


def generate_local_id(device_id: str | None = None) -> str:
    if not device_id:
        device_id = generate_imei()
    return md5_hash(device_id).upper() + "|0"


def get_app_signature_md5() -> str:
    return md5_hash(APP_SIGNATURE)


def generate_moutai_user_agent(
    app_version: str, device_id: str, bs_dvid: str, device_config: dict | None = None
) -> str:
    device_config = device_config or get_random_device_config()
    device_id_md5_first = md5_hash(device_id)
    device_id_double_md5 = md5_hash(device_id_md5_first)
    return (
        f"Mozilla/5.0 (Linux; Android {device_config['android_version']}; "
        f"{device_config['device']} Build/{device_config['build_id']}; wv) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        f"Chrome/128.0.6613.88 Mobile Safari/537.36 moutaiapp/{app_version} "
        f"device-id/{device_id_double_md5} BS-DVID/{bs_dvid}"
    )
