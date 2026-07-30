from __future__ import annotations
import json, os, re, sys
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import py_bb_new

ROOT=str(ROOT)

def load_account():
    data=json.load(open(os.path.join(ROOT,'accounts.json'),encoding='utf-8'))
    arr=data if isinstance(data,list) else data.get('accounts',[])
    for a in arr:
        if a.get('h5_did') and a.get('h5_sid') and (a.get('uid') or a.get('userId') or a.get('user_id')):
            return a
    raise SystemExit('no account')

def cookie_map(raw):
    out={}
    for part in str(raw or '').split(';'):
        if '=' in part:
            k,v=part.strip().split('=',1)
            out[k]=v
    return out

def short(s,n=28):
    s=str(s or '')
    return s[:n]+('...' if len(s)>n else '')

a=load_account()
s=app.Session(
    aid=a.get('aid') or a.get('id') or a.get('phone') or '',
    sdk=a.get('sdk') or a.get('sdk_version') or '',
    mfr=a.get('mfr') or a.get('manufacturer') or '',
    mdl=a.get('mdl') or a.get('model') or '',
    scr=a.get('scr') or a.get('screen') or '',
    btime=a.get('btime') or '',
    bsid=a.get('bsid') or '',
    wv=a.get('wv') or '',
    raw_id=a.get('raw_id') or a.get('rawId') or '',
    h5_did=a.get('h5_did') or '',
    h5_sid=a.get('h5_sid') or '',
)
s._uid=str(a.get('uid') or a.get('userId') or a.get('user_id') or '')
s._token=str(a.get('token') or '')
s._cookie=str(a.get('cookie') or s._token or '')
body={'actParam':'audit-real-header'}
h=s._h5_hdr(body, ref='https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2', rush=True)
ck=cookie_map(h.get('Cookie',''))
web_plain=py_bb_new.decrypt_process_data_hex(h['Content-Web-Bb'])
du_plain=py_bb_new.decrypt_process_data_hex(ck.get('_d_u',''))
print('uid',s._uid)
print('raw_id',short(s._raw_id),'dev_token',short(s._dev_token))
print('h5id',s._h5id,'h5sid',s._h5sid)
print('bsid?',bool(s._bsid),'bs_ck',s._bs_ck)
print('UA contains BS-DVID', 'BS-DVID/' in h.get('User-Agent',''))
print('header User-Agent', short(h.get('User-Agent'),120))
print('Cookie keys', list(ck.keys()))
print('Cookie MT-Device-ID-Wap==dev_token', ck.get('MT-Device-ID-Wap')==s._dev_token)
print('Cookie _bs_device_id==bs_ck', ck.get('_bs_device_id')==s._bs_ck)
print('BB plain', web_plain)
print('DU plain', du_plain)
print('BB did==h5id', web_plain[1]==s._h5id)
print('BB sid==h5sid', web_plain[2]==s._h5sid)
print('BB uid==uid', str(web_plain[8])==str(s._uid))
print('BB wasm140', web_plain[9])
print('BB signWac len', len(str(web_plain[10] or '')), short(web_plain[10],80))
print('DU did==h5id', du_plain[1]==s._h5id)
print('DU sid==h5sid', du_plain[2]==s._h5sid)
print('DU hp==bs_ck', du_plain[5]==s._bs_ck)
print('DU domain', du_plain[4])
