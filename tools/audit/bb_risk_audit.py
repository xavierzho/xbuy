from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import py_bb_new

ROOT = str(ROOT)
NODE = os.environ.get("NODE_EXE") or r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
FIXED_WAC_NOW = 1888888888888


def load_accounts(limit=3):
    with open(os.path.join(ROOT, "accounts.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    arr = data if isinstance(data, list) else data.get("accounts", [])
    out=[]
    for a in arr:
        did = a.get("h5_did") or a.get("devid") or a.get("did")
        sid = a.get("h5_sid") or a.get("sessid") or a.get("sid")
        uid = a.get("uid") or a.get("userId") or a.get("user_id")
        if did and sid and uid:
            out.append(a)
            if len(out)>=limit: break
    return out


def node_compare_du(ctx, now):
    script = r"""
const fs=require('fs');
const bb=require('./node_bridge/bb/bb_module.cjs');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
bb.bbDuCookie(p.ctx, {now:p.now}).then(r=>process.stdout.write(JSON.stringify(r))).catch(e=>{process.stderr.write(e.stack||String(e));process.exit(1);});
"""
    prelude=f"Date.now=()=>{FIXED_WAC_NOW};\n"
    proc=subprocess.run([NODE,'-e',prelude+script],input=json.dumps({'ctx':ctx,'now':now},ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,cwd=ROOT,timeout=30)
    if proc.returncode: raise RuntimeError(proc.stderr)
    return json.loads(proc.stdout)


def node_compare_bb(body, ctx, now, random_id, smk):
    script = r"""
const fs=require('fs');
const bb=require('./node_bridge/bb/bb_module.cjs');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
bb.ensureRuntime().then(()=>bb.bbSignPostForRush(p.body, p.ctx, {now:p.now, randomId:p.randomId})).then(r=>process.stdout.write(JSON.stringify(r))).catch(e=>{process.stderr.write(e.stack||String(e));process.exit(1);});
"""
    ctx=dict(ctx); ctx['smk']=smk
    prelude=f"Date.now=()=>{FIXED_WAC_NOW};\n"
    proc=subprocess.run([NODE,'-e',prelude+script],input=json.dumps({'body':body,'ctx':ctx,'now':now,'randomId':random_id},ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,cwd=ROOT,timeout=30)
    if proc.returncode: raise RuntimeError(proc.stderr)
    return json.loads(proc.stdout)


def short(v,n=18):
    s=str(v or '')
    return s[:n]+('...' if len(s)>n else '')


def main():
    accounts=load_accounts(5)
    print('accounts', len(accounts))
    now=1777777777777
    random_id='0123456789abcdef0123456789abcdef'
    smk='fedcba9876543210fedcba9876543210'
    sign_wasm_b64=base64.b64encode(py_bb_new.load_sign_wasm_bytes()).decode('ascii')
    py_bb_new.time.time=lambda: FIXED_WAC_NOW/1000.0
    for i,a in enumerate(accounts,1):
        did=str(a.get('h5_did') or a.get('devid') or a.get('did') or '')
        sid=str(a.get('h5_sid') or a.get('sessid') or a.get('sid') or '')
        uid=str(a.get('uid') or a.get('userId') or a.get('user_id') or '')
        ua=str(a.get('h5_user_agent') or a.get('user_agent') or a.get('ua') or '')
        hp=str(a.get('bs_ck') or a.get('_bs_device_id') or '')
        if not hp:
            hp='bid-demo-'+did[-4:]
        ctx={'appid':'10001','devid':did,'sessid':sid,'uid':uid,'ua':ua,'hp':hp,'wasmVersion':py_bb_new.DEFAULT_SIGN_WASM_VERSION,'wasmBytesBase64':sign_wasm_b64,'wasmOk':True,'pageDomain':'https://h5.moutai519.com.cn/mt/item/smsp-detail'}
        body={'actParam':'audit-demo','idx':i}
        py_bb=py_bb_new.build_bb_headers_py(body,appid='10001',devid=did,sessid=sid,uid=uid,ua=ua,wasm_version=py_bb_new.DEFAULT_SIGN_WASM_VERSION,now_ms=now+i,random_id=random_id,smk=smk)
        nd_bb=node_compare_bb(body,ctx,now+i,random_id,smk)
        py_du=py_bb_new.build_du_cookie_py(appid='10001',devid=did,sessid=sid,uid=uid,ua=ua,hp=hp,page_domain=ctx['pageDomain'],now_ms=now+i)
        nd_du=node_compare_du(ctx,now+i)['value']
        py_du_plain=py_bb_new.decrypt_process_data_hex(py_du)
        nd_du_plain=py_bb_new.decrypt_process_data_hex(nd_du)
        print('case',i,'uid',uid,'did',short(did),'sid',short(sid),'hp',short(hp))
        for k in ['Content-Web-Bb','Content-Hh-Bb','Sdk-Ver-Bb','randomId','sign','signWac']:
            print((' OK ' if py_bb.get(k)==nd_bb.get(k) else ' BAD'), k, len(str(py_bb.get(k,''))), len(str(nd_bb.get(k,''))))
        print((' OK ' if py_du==nd_du else ' BAD'), '_d_u', len(py_du), len(nd_du), short(py_du), short(nd_du))
        print((' OK ' if py_du_plain==nd_du_plain else ' BAD'), '_d_u_plain', py_du_plain, nd_du_plain)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
