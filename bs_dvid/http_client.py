from __future__ import annotations

import base64
import gzip
import json

from bs_dvid.gzip_util import gzip_deterministic
from bs_dvid.json_compat import dumps_like_node
import logging
import time
import urllib.parse
from typing import Any

import requests

from bs_dvid.constants import APP_KEY, BASE_URL, SEC_KEY
from bs_dvid.crypto_util import (
    aes_decrypt_cbc,
    aes_encrypt_cbc,
    generate_random_key,
    md5_hash,
    rc4_decrypt,
    rc4_encrypt,
)

_log = logging.getLogger(__name__)


def _proxy_dict(proxy_config: dict | None) -> dict[str, str] | None:
    if not proxy_config or not proxy_config.get("enabled"):
        return None
    host = proxy_config.get("host")
    port = proxy_config.get("port")
    if not host or not port:
        return None
    user = proxy_config.get("username") or ""
    pwd = proxy_config.get("password") or ""
    ptype = (proxy_config.get("type") or "http").lower()
    if ptype == "socks5":
        scheme = "socks5h"
    else:
        scheme = "http"
    if user and pwd:
        auth = f"{user}:{pwd}@"
    else:
        auth = ""
    url = f"{scheme}://{auth}{host}:{port}"
    return {"http": url, "https": url}


def build_encrypted_request_artifacts(
    url_path: str,
    body: Any,
    local_id_md5: str,
    *,
    timestamp: str,
    random_key: bytes,
) -> dict:
    sign = md5_hash(APP_KEY + timestamp + SEC_KEY)
    body_json = dumps_like_node(body).encode("utf-8")
    body_gzip = gzip_deterministic(body_json)
    body_encrypted = aes_encrypt_cbc(body_gzip, random_key)
    local_id_md5_bytes = local_id_md5.encode("utf-8")
    skey_encrypted = rc4_encrypt(random_key, local_id_md5_bytes)
    skey_b64 = base64.b64encode(skey_encrypted).decode("ascii") + "\n"
    return {
        "path": url_path,
        "timestamp": timestamp,
        "sign": sign,
        "bodyPlainJson": body_json.decode("utf-8"),
        "bodyEncryptedB64": base64.b64encode(body_encrypted).decode("ascii"),
        "skeyB64": skey_b64.rstrip("\n"),
        "randomKeyAscii": random_key.decode("ascii"),
    }


def send_encrypted_request(
    url_path: str,
    body: Any,
    local_id_md5: str,
    headers: dict[str, str],
    timeout: float = 30.0,
    proxy_config: dict | None = None,
) -> dict:
    timestamp = str(int(time.time()))
    sign = md5_hash(APP_KEY + timestamp + SEC_KEY)
    random_key = generate_random_key(16)

    body_json = dumps_like_node(body).encode("utf-8")
    body_gzip = gzip.compress(body_json, compresslevel=9)
    body_encrypted = aes_encrypt_cbc(body_gzip, random_key)

    local_id_md5_bytes = local_id_md5.encode("utf-8")
    skey_encrypted = rc4_encrypt(random_key, local_id_md5_bytes)
    skey_b64 = base64.b64encode(skey_encrypted).decode("ascii") + "\n"

    full_path = (
        f"{url_path}/{APP_KEY}/{timestamp}/{sign}"
        f"?skey={urllib.parse.quote(skey_b64, safe='')}"
    )
    url = f"https://{BASE_URL}{full_path}"

    req_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Encoding": "gzip,deflate",
        "Accept-Language": "zh-CN",
        "Pragma": "no-cache",
        "x-device-id": local_id_md5,
        "x-auth-ver": "2",
        **headers,
    }

    if proxy_config and proxy_config.get("enabled"):
        _log.info(
            "[BS-DVID] 使用代理: %s:%s",
            proxy_config.get("host"),
            proxy_config.get("port"),
        )

    resp = requests.post(
        url,
        data=body_encrypted,
        headers=req_headers,
        timeout=timeout,
        proxies=_proxy_dict(proxy_config),
        verify=False,
    )
    resp.raise_for_status()

    response_buffer = resp.content
    if resp.headers.get("content-encoding") == "gzip" or (
        len(response_buffer) >= 2 and response_buffer[0] == 0x1F and response_buffer[1] == 0x8B
    ):
        try:
            response_buffer = gzip.decompress(response_buffer)
        except OSError:
            pass

    resp_json = json.loads(response_buffer.decode("utf-8"))

    if resp_json.get("response") and resp_json.get("skey"):
        resp_encrypted = base64.b64decode(resp_json["response"])
        resp_skey_encrypted = base64.b64decode(resp_json["skey"])
        resp_skey = rc4_decrypt(resp_skey_encrypted, local_id_md5_bytes)
        resp_decrypted = aes_decrypt_cbc(resp_encrypted, resp_skey)
        try:
            resp_data = json.loads(resp_decrypted.decode("utf-8"))
            return {
                "success": True,
                "data": resp_data,
                "request_id": resp_json.get("request_id"),
                "raw": resp_json,
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "JSON解析失败",
                "raw_decrypted": resp_decrypted.decode("utf-8", errors="replace"),
            }

    if resp_json.get("data") and resp_json.get("skey"):
        resp_encrypted = base64.b64decode(resp_json["data"])
        resp_skey_encrypted = base64.b64decode(resp_json["skey"])
        resp_skey = rc4_decrypt(resp_skey_encrypted, local_id_md5_bytes)
        resp_decrypted = aes_decrypt_cbc(resp_encrypted, resp_skey)
        try:
            decompressed = gzip.decompress(resp_decrypted)
            resp_data = json.loads(decompressed.decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            resp_data = json.loads(resp_decrypted.decode("utf-8"))
        return {
            "success": True,
            "data": resp_data,
            "request_id": resp_json.get("request_id"),
            "timestamp": resp_json.get("t"),
            "raw": resp_json,
        }

    if resp_json.get("response") == 1:
        return {"success": True, "request_id": resp_json.get("request_id")}

    return {"success": False, "error": "响应格式错误", "raw": resp_json}
