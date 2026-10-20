#!/usr/bin/env python3
"""
多代理 HTTP 出口池：从 local_proxy_pool.txt 加载，遇 HTTP 429 时轮换下一条并重试。

加载时保留重复行，再随机打乱；重复代理会作为独立池条目参与轮询。
默认不再给账号切独占分片；空代理账号统一从整体代理池按全局游标轮询取线，rush 多档与 429/异常换线共用这个游标。

历史账号分片链（chain_for_shard）仅为兼容旧配置保留；默认 Session 不再使用它。
全局轮换链（未用分片的老逻辑）默认：池内 URL 之后仍可追加本机直连空串；勿要直连可设 MT_PROXY_POOL_NO_DIRECT=1（true/yes）。

每行支持（分隔符可用 / 或 |）：
  host/port/user/password  → 规范为 socks5://
  host|port|user|password
  host/port/user/password/2026-04-20 23:57:22   （过期时间，过期行忽略）
  host|port|user|password|2026-04-20 23:57:22   （过期时间，过期行忽略）
  仅 IP/域名 + 端口（无账密）：`host:port` 或 `host/port`、`host|port`，默认规范为 `socks5://`；
显式 URL：socks5://、socks5h://、http://、https://（可带或不带 user:pass@）

代理池**保存**仅做格式解析、直接落盘；连通检测请用 WebUI「自检连通」或 verify_pool_urls / MT_PROXY_POOL_VERIFY_TIMEOUT（默认 14s）。
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from settings.paths import PATHS

POOL_FILE = PATHS.proxy_pool_txt

# 误写 host:18081:18081 时 urlparse().port 会抛 ValueError；仅折叠末尾「相同端口重复」一段。
_DUP_TRAIL_PORT = re.compile(r"^(.+):(?P<p>\d{1,5}):(?P=p)$")


def sanitize_proxy_url(url: str) -> str:
    """
    修正 netloc 末尾重复的 :port:port（常见于手误多粘一次端口）。
    例：socks5://u:p@1.2.3.4:18081:18081 → …@1.2.3.4:18081
    """
    u = (url or "").strip()
    if not u or "://" not in u:
        return u
    scheme, rest = u.split("://", 1)
    sl = scheme.lower()
    # 兼容口语/面板别名：ks5/ks5h/socks5s 默认都按 socks5 处理。
    if sl in ("ks5", "ks5h", "socks5s"):
        scheme = "socks5"
    slash = rest.find("/")
    if slash >= 0:
        netloc, path = rest[:slash], rest[slash:]
    else:
        netloc, path = rest, ""
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
    else:
        userinfo, hostport = "", netloc
    hp = hostport
    while True:
        m = _DUP_TRAIL_PORT.match(hp)
        if not m:
            break
        hp = f"{m.group(1)}:{m.group('p')}"
    if hp == hostport:
        return u
    netloc = f"{userinfo}@{hp}" if userinfo else hp
    return f"{scheme}://{netloc}{path}"


_lock = threading.Lock()
_urls: list[str] = []
_idx: int = 0  # 指向下一条要发放/轮换的出口
_loaded_mtime: float | None = None
_runtime_override_urls: list[str] | None = None
# 批量改订单地址：按池顺序取线，与 429 全局 _idx、账号分片映射独立
_batch_addr_ring_i: int = 0


def _proxy_log_enabled() -> bool:
    return (os.environ.get("MT_PROXY_LOG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "verbose",
    )


def _proxy_endpoint_brief(url: str) -> str:
    """日志用：user@host:port 或 直连，不含密码。"""
    if not (url or "").strip():
        return "直连(本机)"
    p = urlparse(sanitize_proxy_url(url))
    host = p.hostname or "?"
    port = p.port or 80
    user = (p.username or "").strip()
    pref = f"{user}@" if user else ""
    return f"{pref}{host}:{port}"


def _rotation_include_direct() -> bool:
    """
    是否在轮换链末尾追加空串（本机直连）。
    默认 True；设 MT_PROXY_POOL_NO_DIRECT=1 关闭（仅池内代理轮询）。
    兼容旧变量：MT_PROXY_ALLOW_DIRECT=0/false/no 等价于关闭直连。
    """
    no_direct = (os.environ.get("MT_PROXY_POOL_NO_DIRECT") or "").strip().lower()
    if no_direct in ("1", "true", "yes"):
        return False
    legacy = (os.environ.get("MT_PROXY_ALLOW_DIRECT") or "").strip().lower()
    if legacy in ("0", "false", "no", "off"):
        return False
    return True


def _pool_bare_hostport_scheme() -> str:
    """
    纯 host:port 或 host/port 行默认补全的协议。
    socks5（默认）；可设 MT_PROXY_POOL_HOSTPORT_SCHEME=socks5h 切到远端 DNS。
    """
    s = (os.environ.get("MT_PROXY_POOL_HOSTPORT_SCHEME") or "socks5").strip().lower()
    if s in ("socks5h", "ks5h", "socks5s"):
        return "socks5h"
    return "socks5"


def _host_port_to_bare_proxy_url(host: str, port_s: str) -> str | None:
    host = (host or "").strip()
    if not host:
        return None
    try:
        pnum = int(port_s)
    except (TypeError, ValueError):
        return None
    if pnum < 1 or pnum > 65535:
        return None
    sch = _pool_bare_hostport_scheme()
    netloc = f"{host}:{port_s}"
    pr = urlparse(f"{sch}://{netloc}")
    if not pr.hostname or pr.port is None:
        return None
    return sanitize_proxy_url(f"{sch}://{netloc}")


def _parse_expire_suffix(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            dt = datetime.strptime(s[:n], fmt)
            # 仅日期时按「当天仍有效」：比较用当日 23:59:59.999999，避免 4-10 当天零点起整批被判过期
            if n == 10:
                return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt
        except Exception:
            continue
    return None


def _line_to_proxy_url(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        u = line.strip()
        low = u.lower()
        if not (
            low.startswith("socks5://")
            or low.startswith("socks5h://")
            or low.startswith("ks5://")
            or low.startswith("ks5h://")
            or low.startswith("socks5s://")
            or low.startswith("http://")
            or low.startswith("https://")
        ):
            return None
        try:
            pr = urlparse(u)
        except ValueError:
            return None
        if not pr.hostname or pr.port is None:
            return None
        return sanitize_proxy_url(u)

    # 兼容两种分隔符：a/b/c/d[/expire] 或 a|b|c|d[|expire]
    m = re.match(r"^([^/|\s]+)([/|])(\d+)\2([^/|]+)\2([^/|]+)(?:\2(.+))?$", line)
    if m:
        host, port, user, pwd, expire_rest = m.group(1), m.group(3), m.group(4), m.group(5), m.group(6)
        if expire_rest:
            exp = _parse_expire_suffix(expire_rest)
            if exp and datetime.now() > exp:
                return None
        u = quote(user, safe="")
        p = quote(pwd, safe="")
        return sanitize_proxy_url(f"socks5://{u}:{p}@{host}:{port}")

    # host/port[/expire] 或 host|port[|expire] — 仅端口与第二段，无账密
    m_slash = re.match(r"^([^/|\s]+)[/|](\d{1,5})(?:[/|](.+))?$", line)
    if m_slash:
        host, port_s, expire_rest = m_slash.group(1), m_slash.group(2), m_slash.group(3)
        if expire_rest:
            exp = _parse_expire_suffix(expire_rest)
            if exp and datetime.now() > exp:
                return None
        out = _host_port_to_bare_proxy_url(host, port_s)
        if out:
            return out

    # host:port[/expire] 或 [v6]:port[/expire] — 无账密（IPv6 请写 [addr]:port）
    m_colon = re.match(r"^(\[[^\]]+\]|[^:/\s]+):(\d{1,5})(?:[/|](.+))?$", line)
    if m_colon:
        host, port_s, expire_rest = m_colon.group(1), m_colon.group(2), m_colon.group(3)
        if expire_rest:
            exp = _parse_expire_suffix(expire_rest)
            if exp and datetime.now() > exp:
                return None
        out = _host_port_to_bare_proxy_url(host, port_s)
        if out:
            return out

    return None


def line_to_proxy_url(line: str) -> str | None:
    """供 UI 与外部工具复用的单行解析（与池文件规则一致）。"""
    return _line_to_proxy_url(line)


_MT_H5_PROBE_HOST = "h5.moutai519.com.cn"
_MT_H5_PROBE_PORT = 443
# 与 app.TLS_PROFILE 一致：代理自检统一 curl_cffi 指纹，避免与茅台主链路分裂
_CURL_TLS_IMPERSONATE = "safari17_0"


def verify_pool_proxy_url(url: str, timeout: float = 14.0) -> tuple[bool, str]:
    """
    自检一条代理：支持 SOCKS5/SOCKS5H/HTTP/HTTPS，经代理访问 i 茅台 H5。
    返回 (是否成功, 说明)。
    """
    u = sanitize_proxy_url((url or "").strip())
    if not u:
        return False, "empty"
    pr = urlparse(u)
    scheme = (pr.scheme or "").lower()
    host_p, port_p = pr.hostname, pr.port
    user = unquote(pr.username) if pr.username else ""
    pwd = unquote(pr.password) if pr.password else ""
    if not host_p or not port_p:
        return False, "invalid_url"
    t = max(3.0, float(timeout or 14.0))

    if scheme in ("socks5", "socks5h", "http", "https"):
        # 与 Node 桥实际出口一致：优先经代理发完整 HTTPS（curl_cffi）；仅 TCP 通 443 时易「自检过、业务 socket hang up」
        try:
            from curl_cffi import requests as creq
        except ImportError:
            creq = None
        if creq is not None:
            try:
                proxies = {"http": u, "https": u}
                r = creq.get(
                    f"https://{_MT_H5_PROBE_HOST}/",
                    proxies=proxies,
                    timeout=t,
                    impersonate=_CURL_TLS_IMPERSONATE,
                )
                if r.status_code in (200, 301, 302, 404):
                    return True, f"{scheme}_https:{r.status_code}"
                return False, f"{scheme}_https:{r.status_code}"
            except Exception as e:
                return False, str(e).strip()[:220] or f"{scheme}_https_fail"
        if scheme in ("http", "https"):
            return False, "缺少 curl_cffi，无法检测 HTTP 代理"
        try:
            import socks
        except ImportError:
            return False, "缺少 PySocks 与 curl_cffi，无法检测 SOCKS 代理"
        s = socks.socksocket()
        try:
            s.set_proxy(
                socks.SOCKS5,
                host_p,
                int(port_p),
                rdns=(scheme == "socks5h"),
                username=user or None,
                password=pwd or None,
            )
            s.settimeout(t)
            s.connect((_MT_H5_PROBE_HOST, _MT_H5_PROBE_PORT))
            s.close()
            return True, "socks5_tcp_only(建议安装curl_cffi以HTTPS自检)"
        except Exception as e:
            try:
                s.close()
            except OSError:
                pass
            return False, str(e).strip()[:220] or "socks5_fail"

    return False, f"unsupported:{scheme or '?'}(only_socks5_http)"


def verify_pool_urls(urls: list[str], timeout: float | None = None, max_workers: int = 8) -> list[dict]:
    """
    按输入顺序返回每条检测结果；endpoint 脱敏展示（user@host:port）。
    """
    if not urls:
        return []
    try:
        env_to = (os.environ.get("MT_PROXY_POOL_VERIFY_TIMEOUT") or "").strip()
        to = float(timeout if timeout is not None else (env_to or "14"))
    except (TypeError, ValueError):
        to = 14.0
    to = max(3.0, to)
    workers = max(1, min(int(max_workers or 8), len(urls)))

    def one(idx: int, u: str) -> dict:
        ok, msg = verify_pool_proxy_url(u, timeout=to)
        return {
            "index": idx,
            "endpoint": _proxy_endpoint_brief(u),
            "ok": ok,
            "message": msg,
        }

    if len(urls) == 1:
        return [one(0, urls[0])]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        indexed = [(i, ex.submit(one, i, urls[i])) for i in range(len(urls))]
        for i, fut in indexed:
            try:
                results.append(fut.result(timeout=to + 60.0))
            except Exception as e:
                results.append(
                    {
                        "index": i,
                        "endpoint": _proxy_endpoint_brief(urls[i]),
                        "ok": False,
                        "message": str(e)[:220],
                    },
                )
    results.sort(key=lambda r: int(r.get("index") or 0))
    return results


def _reload_if_needed() -> None:
    global _urls, _loaded_mtime
    if _runtime_override_urls is not None:
        _urls = list(_runtime_override_urls)
        _loaded_mtime = None
        return
    if not POOL_FILE.is_file():
        _urls = []
        _loaded_mtime = None
        return
    try:
        mtime = POOL_FILE.stat().st_mtime
    except OSError:
        _urls = []
        return
    if _loaded_mtime == mtime and _urls:
        return

    out: list[str] = []
    try:
        for raw in POOL_FILE.read_text(encoding="utf-8").splitlines():
            u = _line_to_proxy_url(raw)
            if u:
                out.append(u)
    except Exception:
        out = []
    shuffled = list(out)
    if len(shuffled) > 1:
        random.SystemRandom().shuffle(shuffled)
        if _proxy_log_enabled():
            print(
                f"[代理池] 已随机打乱分配顺序：{len(shuffled)} 条（不按文件/API保存顺序切片）",
                flush=True,
            )
    _urls = shuffled
    _loaded_mtime = mtime


def reload_now() -> list[str]:
    """Force re-read local_proxy_pool.txt and return active proxy URLs."""
    global _loaded_mtime
    _loaded_mtime = None
    _reload_if_needed()
    return list(_urls)


def set_runtime_urls(lines: Sequence[str] | None, *, start_offset: int = 0) -> list[str]:
    """Override the pool in memory for distributed jobs; pass None to restore file-backed mode."""
    global _runtime_override_urls, _urls, _loaded_mtime, _idx, _batch_addr_ring_i
    if lines is None:
        _runtime_override_urls = None
        _loaded_mtime = None
        _reload_if_needed()
        return list(_urls)
    try:
        cursor = int(start_offset or 0)
    except (TypeError, ValueError):
        cursor = 0
    out: list[str] = []
    for raw in list(lines or []):
        u = _line_to_proxy_url(str(raw or ""))
        if u:
            out.append(u)
    with _lock:
        _runtime_override_urls = list(out)
        _urls = list(out)
        _loaded_mtime = None
        _idx = cursor % len(_urls) if _urls else 0
        _batch_addr_ring_i = 0
    return list(_urls)


def pool_is_configured() -> bool:
    _reload_if_needed()
    return len(_urls) > 0


def active_urls() -> list[str]:
    """仅代理池文件中的 URL（不含本机直连）。"""
    _reload_if_needed()
    return list(_urls)


def next_active_pool_url_for_batch_order_addr() -> str:
    """
    批量改订单地址专用：在 active_urls 上顺序取「下一条」池内代理（与账号分片/按号稳定映射无关）。

    与 rotate_next_url 的全局 _idx 独立，避免与 429 轮换抢同一计数；线程安全。
    """
    _reload_if_needed()
    with _lock:
        global _batch_addr_ring_i
        if not _urls:
            return ""
        u = _urls[_batch_addr_ring_i % len(_urls)]
        _batch_addr_ring_i += 1
        return u


def rotation_chain() -> list[str]:
    """
    429/异常轮换用：先池中各 URL，再视配置追加本机直连（空串）；见 _rotation_include_direct()。
    无有效代理文件时仅返回本机直连 [""]。
    """
    _reload_if_needed()
    if not _urls:
        return [""]
    if _rotation_include_direct():
        return list(_urls) + [""]
    return list(_urls)


def rotation_count() -> int:
    return len(rotation_chain())


def _take_from_chain_locked(chain: list[str], count: int) -> list[str]:
    """从传入链上按全局游标连续取 count 条，并推进游标；调用方需持有 _lock。"""
    global _idx
    if not chain:
        return []
    try:
        n = max(1, int(count or 1))
    except (TypeError, ValueError):
        n = 1
    start = _idx % len(chain)
    out = [chain[(start + i) % len(chain)] for i in range(n)]
    _idx = (start + n) % len(chain)
    return out


def take_active_pool_urls(count: int) -> list[str]:
    """
    从整体代理池按全局游标连续取 N 条池内代理（不含本机直连）。
    N <= 池大小时本次返回不重复；N > 池大小时只能循环复用。
    """
    _reload_if_needed()
    with _lock:
        if not _urls:
            return []
        return _take_from_chain_locked(list(_urls), count)


def next_active_pool_url() -> str:
    """从整体代理池取下一条池内代理（不含本机直连），并推进全局游标。"""
    urls = take_active_pool_urls(1)
    return urls[0] if urls else ""


def pool_current_url() -> str:
    """当前全局游标指向的下一出口：可能为空串（本机直连）或某一代理 URL（不前进下标）。"""
    _reload_if_needed()
    chain = rotation_chain()
    if not chain:
        return ""
    with _lock:
        return chain[_idx % len(chain)]


def rotate_next_url() -> str:
    """429 后从全局轮换链取下一跳；可能为本机或下一代理，并推进游标。"""
    _reload_if_needed()
    chain = rotation_chain()
    if not chain:
        return ""
    with _lock:
        url = _take_from_chain_locked(chain, 1)[0]
        if _proxy_log_enabled():
            print(
                f"[代理池] 轮换 → 序位 {_idx} 出口 {_proxy_endpoint_brief(url)}",
                flush=True,
            )
        return url


def get_session_proxy_url() -> str:
    """新建 Session 时从整体代理池顺序取一条，避免多账号同时落到同一池首。"""
    return next_active_pool_url()


def pick_proxy_url_for_key(stable_key: str) -> str:
    """
    从池中稳定映射一条「已带账号密码」的完整 URL（socks5://）。
    供多账号分散出口；key 建议用手机号，保证同一账号持久对应同一条池内代理。
    """
    key = (stable_key or "").strip()
    if not key:
        key = "_"
    _reload_if_needed()
    if not _urls:
        return ""
    if len(_urls) == 1:
        return _urls[0]
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return _urls[h % len(_urls)]


def pick_proxy_url_for_raw_id(raw_id: str) -> str:
    """无账号上下文时（单 Session）用设备 raw_id 映射池内一条。"""
    rid = (raw_id or "").strip() or "_"
    return pick_proxy_url_for_key(f"rid:{rid}")


def pick_proxy_url_by_index(acct_index: int, total_accts: int) -> str:
    """按当前账号序位均分后，取该账号分片的首条代理。"""
    shard = shard_urls_by_index(acct_index, total_accts)
    if shard:
        return shard[0]
    _reload_if_needed()
    if not _urls:
        return ""
    return _urls[0]


def shard_urls_for_phone(norm_phone: str, sorted_norm_phones: Sequence[str]) -> list[str]:
    """
    按账号数将池中 M 条代理**无重叠**切成 N 段（按传入列表顺序的序号 j）。
    段长用 ((j+1)*M//N - j*M//N)，余数摊到不同账号而非堆在排序最前。
    单账号或未传其他号时返回整池；分片为空时返回 []（常见于 M < N）。
    池中只有 1 条时所有账号共用整池（隧道代理场景，每次连接自动换 IP）。
    """
    _reload_if_needed()
    if not _urls:
        return []
    if len(_urls) == 1:
        return list(_urls)
    phones: list[str] = []
    seen: set[str] = set()
    for p in list(sorted_norm_phones or []):
        ph0 = str(p).strip()
        if not ph0 or ph0 in seen:
            continue
        seen.add(ph0)
        phones.append(ph0)
    if len(phones) <= 1:
        return list(_urls)
    ph = str(norm_phone or "").strip()
    try:
        j = phones.index(ph)
    except ValueError:
        j = 0
    n, m = len(phones), len(_urls)
    # 整数均分 M→N：每人仅 ⌊M/N⌋ 或 ⌈M/N⌉ 条，多出的分散到不同 j（非「前 rem 个号连吃余数」）
    lengths = [((k + 1) * m) // n - (k * m) // n for k in range(n)]
    start = sum(lengths[:j])
    ln = lengths[j]
    shard = _urls[start : start + ln]
    if _proxy_log_enabled() and not shard:
        print(
            f"[代理池] 分片为空：账号序 {j + 1}/{n}（{ph!r}），池 {m} 条 < 账号数或序位靠后无独占线",
            flush=True,
        )
    return shard


def shard_urls_by_index(acct_index: int, total_accts: int) -> list[str]:
    """
    按账号序号（0-based）直接切分代理池，无需传手机号列表。
    第 j 个账号（acct_index=j，total_accts=N）独占池中第 j 段：
      start = (j * M) // N
      end   = ((j+1) * M) // N
    余数自动分散到不同账号，保证每账号互不重叠。
    M < N 时部分账号分片为空，返回 []。
    池中只有 1 条时所有账号共用整池（隧道代理场景，每次连接自动换 IP）。
    """
    _reload_if_needed()
    if not _urls:
        return []
    if len(_urls) == 1:
        return list(_urls)
    n = max(1, int(total_accts or 1))
    j = max(0, min(int(acct_index or 0), n - 1))
    m = len(_urls)
    if n == 1:
        return list(_urls)
    start = (j * m) // n
    end = ((j + 1) * m) // n
    shard = _urls[start:end]
    if _proxy_log_enabled() and not shard:
        print(
            f"[代理池] 分片为空：账号序 {j + 1}/{n}，池 {m} 条 < 账号数",
            flush=True,
        )
    return shard


def chain_for_shard(shard: list[str]) -> list[str]:
    """
    分片内仅保留认证代理 URL，供单 Session 内循环轮换；不追加本机直连，
    避免抢购/组单等在换线时落到本机出口与账号分配 IP 不一致。
    """
    _reload_if_needed()
    return [u for u in shard if (u or "").strip()]
