import base64
import json
import sys
import time
from types import SimpleNamespace

import pytest

import accounts
import address_db


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(address_db, "DB_PATH", tmp_path / "addresses.sqlite3")
    monkeypatch.setattr(address_db, "_schema_ok", False)
    monkeypatch.delenv("MT_DIST_MEMORY_ONLY", raising=False)
    monkeypatch.delenv("MT_DISABLE_ADDRESS_DB", raising=False)
    monkeypatch.setitem(sys.modules, "state_file_backup", SimpleNamespace(backup_before_write=lambda _: None))
    monkeypatch.setitem(sys.modules, "webui_server", SimpleNamespace(sync_api_listen_ports=lambda: None))
    return accounts.AccountPool()


def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.fixture-signature"


def export(*rows):
    return json.dumps({"app": "iMT", "groups": ["测试"], "accounts": rows})


def test_imt_import_persists_login_and_address(pool):
    now = int(time.time())
    token, cookie = jwt(now + 3600), jwt(now + 7200)
    address = {"shipAddressId": 42, "name": "收件人", "dft": True}
    row = {
        "mobile": "13800138001",
        "userId": 12345,
        "userName": "账号昵称",
        "verifyStatus": 1,
        "token": token,
        "cookie": cookie,
        "deviceHeaders": {
            "deviceIdPlain": "a" * 32,
            "simpleUserAgent": "android;34;vivo;v2254a",
            "userAgent": "fixture UA",
            "mtRs": "1080*2400",
        },
        "shippingAddress": address,
        "proxyUrl": "http://example.invalid:8080",
    }
    result = pool.import_mtm_text(export(row))
    assert (result["added"], result["invalid"], result["addressSaved"]) == (1, 0, 1)
    entry = accounts.AccountPool()._map[row["mobile"]]
    assert (entry.token, entry.cookie, entry.status) == (token, cookie, accounts.STATUS_OK)
    assert (entry.uid, entry.nickname, entry.verify_status) == ("12345", "账号昵称", 1)
    assert (entry.aid, entry.sdk, entry.mfr, entry.scr, entry.h5_user_agent) == (
        "a" * 32, "34", "vivo", "1080*2400", "fixture UA",
    )
    assert not entry.proxy
    assert address_db.get_cached_address_list(row["mobile"]) == [address]

    replacement = {**row, "token": jwt(now + 9000)}
    assert pool.import_mtm_text(export(replacement))["skipped"] == 1
    assert pool._map[row["mobile"]].token == token
    assert pool.import_mtm_text(export(replacement), overwrite=True)["updated"] == 1
    assert accounts.AccountPool()._map[row["mobile"]].token == replacement["token"]


def test_imt_reports_invalid_rows_without_overwriting_existing_login(pool):
    phone = "13800138001"
    token = jwt(time.time() + 3600)
    row = {"mobile": phone, "token": token}
    pool.import_mtm_text(export(row))
    result = pool.import_mtm_text(export(
        {**row, "kicked": True},
        {**row, "tokenAlive": False},
        {**row, "token": jwt(1)},
        {"mobile": phone},
        None,
        {**row, "mobile": "13800138002"},
    ), overwrite=True)
    assert (result["added"], result["updated"], result["invalid"]) == (1, 0, 5)
    assert pool._map[phone].token == token
    assert [item["index"] for item in result["invalidItems"]] == [1, 2, 3, 4, 5]
    assert "已过期" in result["invalidItems"][2]["reason"]
    assert not pool.import_mtm_text('{"app":"iMT","accounts":[')["ok"]


def test_legacy_mtm_still_accepts_plain_json_and_encoded_mixed_text(pool):
    first = {"mobile": "13800138001", "token": jwt(1)}
    assert pool.import_mtm_text(json.dumps(first))["added"] == 1
    second = {**first, "mobile": "13800138002"}
    encoded = base64.urlsafe_b64encode(json.dumps(second).encode()).decode()[::-1]
    assert pool.import_mtm_text(f"账号包：MTM-{encoded}")["added"] == 1
