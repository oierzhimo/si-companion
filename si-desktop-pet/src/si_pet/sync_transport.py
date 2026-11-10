"""Opt-in LAN exchange: pinned TLS, expiring invitations and revocable peer keys."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .sync_v1 import DEVICE_ID, MAX_BUNDLE_BYTES, SyncError, canonical


class TransportError(ValueError):
    pass


def local_address(value):
    try:
        address=ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise TransportError('请输入局域网 IPv4 地址') from exc
    if not any(address in ipaddress.IPv4Network(network) for network in
               ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8')):
        raise TransportError('仅支持本机或局域网地址')
    return str(address)


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_bytes(canonical(value))
    os.replace(temporary,path)


def certificate(directory):
    """Make a private local identity; clients trust its transferred fingerprint."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    cert_path=directory/'certificate.pem'
    key_path=directory/'private-key.pem'
    if cert_path.exists()!=key_path.exists():
        raise TransportError('本机配对证书不完整，请保留文件后检查')
    if not cert_path.exists():
        key=ec.generate_private_key(ec.SECP256R1())
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Si local companion')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
              .sign(key,hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert=x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint=hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return cert_path,key_path,fingerprint


class PeerRegistry:
    def __init__(self, directory, device_id, name):
        if not DEVICE_ID.fullmatch(device_id):
            raise TransportError('Invalid local device ID')
        self.directory=Path(directory)
        self.device_id=device_id
        self.name=str(name).strip()[:60] or '我的电脑'
        self.lock=threading.RLock()
        self.path=self.directory/'peers.json'
        self.peers=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if (not isinstance(self.peers,dict) or any(
                not isinstance(device,str) or not DEVICE_ID.fullmatch(device)
                or not isinstance(peer,dict) or set(peer)!={'name','key'}
                or not isinstance(peer['name'],str) or not 1<=len(peer['name'])<=60
                or not isinstance(peer['key'],str) or not 32<=len(peer['key'])<=128
                for device,peer in self.peers.items())):
            raise TransportError('配对存档格式不正确')
        self.invitation=None

    def offer(self,host,port,fingerprint,lifetime=180):
        with self.lock:
            self.invitation={'token':secrets.token_urlsafe(32),'deadline':time.monotonic()+lifetime,
                             'attempts':0}
            return canonical({'transport':1,'host':local_address(host),'port':port,
                              'fingerprint':fingerprint,'device_id':self.device_id,
                              'name':self.name,'invitation':self.invitation['token']}).decode('utf-8')

    def accept(self,token,device_id,name):
        with self.lock:
            invite=self.invitation
            if not invite or time.monotonic()>invite['deadline']:
                self.invitation=None
                raise TransportError('配对邀请已失效')
            if (not isinstance(token,str) or not hmac.compare_digest(token,invite['token'])):
                invite['attempts']+=1
                if invite['attempts']>=5:
                    self.invitation=None
                raise TransportError('配对邀请不正确')
            if (not isinstance(device_id,str) or not DEVICE_ID.fullmatch(device_id)
                    or device_id==self.device_id or not isinstance(name,str) or not 1<=len(name)<=60):
                raise TransportError('对方设备信息不正确')
            key=secrets.token_urlsafe(32)
            previous=self.peers.copy()
            self.peers[device_id]={'name':name,'key':key}
            try:
                write_json(self.path,self.peers)
            except OSError:
                self.peers=previous
                raise
            self.invitation=None
            return {'transport':1,'device_id':self.device_id,'name':self.name,'key':key}

    def authorized(self,device_id,key):
        with self.lock:
            peer=self.peers.get(device_id)
            return bool(peer and isinstance(key,str) and hmac.compare_digest(peer['key'],key))

    def revoke(self,device_id):
        with self.lock:
            previous=self.peers.copy()
            self.peers.pop(device_id,None)
            try:
                write_json(self.path,self.peers)
            except OSError:
                self.peers=previous
                raise


class LanServer:
    """Network worker invokes exchange(bytes); GUI must dispatch it to its owner thread."""
    def __init__(self,registry,exchange,host='127.0.0.1',port=0):
        self.registry=registry
        cert,key,self.fingerprint=certificate(registry.directory)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(str(cert),str(key))
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            def get_request(self):
                raw,address=self.socket.accept()
                raw.settimeout(8)
                try:
                    return tls.wrap_socket(raw,server_side=True),address
                except Exception:
                    raw.close()
                    raise

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass  # Never put invitations or authorization into logs.

            def send_data(self,status,body):
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                try:
                    if self.path not in ('/v1/pair','/v1/exchange'):
                        self.send_data(404,canonical({'error':'Unknown endpoint'}));return
                    if self.path=='/v1/exchange':
                        device=self.headers.get('X-Si-Device','')
                        auth=self.headers.get('Authorization','')
                        if not auth.startswith('Bearer ') or not registry.authorized(device,auth[7:]):
                            self.send_data(401,canonical({'error':'配对已失效，请重新配对'}));return
                    size=int(self.headers.get('Content-Length','-1'))
                    maximum=4096 if self.path=='/v1/pair' else MAX_BUNDLE_BYTES
                    if not 1<=size<=maximum or self.headers.get('Transfer-Encoding'):
                        self.send_data(413,canonical({'error':'请求大小不正确'}));return
                    body=self.rfile.read(size)
                    if len(body)!=size:
                        raise TransportError('请求未完整传送')
                    if self.path=='/v1/pair':
                        request=json.loads(body)
                        response=registry.accept(request.get('invitation'),request.get('device_id'),request.get('name'))
                        self.send_data(200,canonical(response))
                    else:
                        payload=json.loads(body)
                        if payload.get('source_device')!=device:
                            raise TransportError('事件来源与配对设备不一致')
                        self.send_data(200,exchange(body))
                except (ValueError,TypeError,AttributeError,SyncError) as exc:
                    self.send_data(409,canonical({'error':str(exc)[:240]}))
                except (OSError,TimeoutError):
                    self.send_data(503,canonical({'error':'记录暂未完成，请稍后重试'}))

        if host!='0.0.0.0':
            local_address(host)
        self.server=Server((host,port),Handler)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,name='SiLanServer',daemon=True)
        self.thread.start()

    def invitation(self,host,lifetime=180):
        return self.registry.offer(host,self.port,self.fingerprint,lifetime)

    def close(self):
        self.registry.invitation=None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def parse_invitation(text):
    if not isinstance(text,str) or len(text)>4096:
        raise TransportError('配对邀请格式不正确')
    try:
        value=json.loads(text)
        if (not isinstance(value,dict) or set(value)!={'transport','host','port','fingerprint','device_id','name','invitation'}
                or type(value.get('transport')) is not int or value['transport']!=1):
            raise ValueError()
        value['host']=local_address(value['host'])
        if (type(value['port']) is not int or not 1<=value['port']<=65535
                or len(value['fingerprint'])!=64 or any(c not in '0123456789abcdef' for c in value['fingerprint'])
                or not DEVICE_ID.fullmatch(value['device_id'])
                or not isinstance(value['name'],str) or not 1<=len(value['name'])<=60
                or not isinstance(value['invitation'],str) or not 32<=len(value['invitation'])<=128):
            raise ValueError()
    except (ValueError,KeyError,TypeError) as exc:
        raise TransportError('配对邀请格式不正确') from exc
    return value


def request(peer,path,body,device_id='',key=''):
    host=local_address(peer['host'])
    # The exact certificate fingerprint is authenticated before any secret is sent.
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.check_hostname=False
    tls.verify_mode=ssl.CERT_NONE
    connection=http.client.HTTPSConnection(host,int(peer['port']),context=tls,timeout=12)
    try:
        connection.connect()
        fingerprint=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(fingerprint,peer['fingerprint']):
            raise TransportError('对方证书已变化，请重新核对并配对')
        headers={'Content-Type':'application/json'}
        if key:
            headers.update({'Authorization':'Bearer '+key,'X-Si-Device':device_id})
        connection.request('POST',path,body=body,headers=headers)
        response=connection.getresponse()
        data=response.read(MAX_BUNDLE_BYTES+1)
        if len(data)>MAX_BUNDLE_BYTES:
            raise TransportError('对方数据超过大小限制')
        if response.status!=200:
            try:
                message=json.loads(data).get('error','同步未完成')
            except (ValueError,AttributeError):
                message='同步未完成'
            raise TransportError(str(message)[:240])
        return data
    except (OSError,http.client.HTTPException) as exc:
        raise TransportError('无法连接对方，请检查地址、网络和对方程序') from exc
    finally:
        connection.close()


def pair(invitation,device_id,name):
    peer=parse_invitation(invitation)
    response=json.loads(request(peer,'/v1/pair',canonical({
        'invitation':peer['invitation'],'device_id':device_id,'name':str(name)[:60]})))
    if response.get('device_id')!=peer['device_id'] or not isinstance(response.get('key'),str):
        raise TransportError('对方配对响应不正确')
    return {key:peer[key] for key in ('host','port','fingerprint','device_id','name')} | {'key':response['key']}


def exchange(peer,device_id,bundle):
    response=request(peer,'/v1/exchange',bundle,device_id,peer['key'])
    try:
        if json.loads(response).get('source_device')!=peer['device_id']:
            raise TransportError('对方事件来源与配对信息不一致')
    except (ValueError,AttributeError) as exc:
        raise TransportError('对方事件包格式不正确') from exc
    return response
