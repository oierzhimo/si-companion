import socket from '@ohos.net.socket';
import util from '@ohos.util';
import cryptoFramework from '@ohos.security.cryptoFramework';
import { BusinessError } from '@ohos.base';
import { Invitation, Peer, HttpReply, Transport, validateAddress, utf8Length } from '../core/Wire';
import { HttpBuffer } from '../core/HttpBuffer';

export function encode(text: string): Uint8Array { return new util.TextEncoder().encodeInto(text); }
export function decode(data: Uint8Array): string { return util.TextDecoder.create('utf-8', { fatal: true }).decodeToString(data); }
export async function digestBytes(data: Uint8Array): Promise<string> {
  const md = cryptoFramework.createMd('SHA256');await md.update({ data: data });
  const result = await md.digest();let hex = '';
  for (let i = 0; i < result.data.length; i++) hex += result.data[i].toString(16).padStart(2, '0');
  return hex;
}
export async function sha256(text: string): Promise<string> { return digestBytes(encode(text)); }

/** Authenticate this one socket by the transferred certificate pin before sending HTTP bytes. */
export class NativeTls implements Transport {
  async request(peer: Invitation | Peer, path: string, body: string, deviceId: string = '', key: string = ''): Promise<HttpReply> {
    validateAddress(peer.host);
    if (!Number.isInteger(peer.port) || peer.port < 1 || peer.port > 65535 || !/^[0-9a-f]{64}$/.test(peer.fingerprint)) throw new Error('配对连接信息不正确');
    if (path !== '/v1/pair' && path !== '/v1/exchange') throw new Error('Unknown endpoint');
    const tls = socket.constructTLSSocketInstance();
    let authenticated = false, settled = false, timer = 0;
    let resolveResponse: (value: HttpReply) => void = (_value: HttpReply): void => {};
    let rejectResponse: (error: Error) => void = (_error: Error): void => {};
    const response = new Promise<HttpReply>((resolve, reject) => { resolveResponse = resolve;rejectResponse = reject; });
    // Connection errors may arrive before the connect promise settles.
    response.catch((_error: Error): void => {});
    const parser = new HttpBuffer(decode);
    const fail = (error: Error): void => { if (!settled) { settled = true;rejectResponse(error); } };
    tls.on('message', (message: socket.SocketMessageInfo): void => {
      if (settled) return;
      if (!authenticated) { fail(new Error('对方在证书核对前发送了数据'));return; }
      try {
        const reply = parser.push(new Uint8Array(message.message));
        if (reply !== undefined) { settled = true;resolveResponse(reply); }
      } catch (error) { fail(new Error('对方响应不完整或格式不正确')); }
    });
    tls.on('error', (_error: BusinessError): void => { fail(new Error('无法连接电脑，请检查局域网和对方接收服务')); });
    tls.on('close', (): void => { if (!settled) fail(new Error('对方在传输完成前关闭了连接')); });
    const timeout = new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { reject(new Error('连接超时，请稍后重试')); }, 12000); });
    try {
      await Promise.race([tls.connect({ address: { address: peer.host, port: peer.port, family: 1 },
        secureOptions: { protocols: [socket.Protocol.TLSv12, socket.Protocol.TLSv13] }, skipRemoteValidation: true,
        ALPNProtocols: ['http/1.1'] }), timeout]);
      const certificate = await Promise.race([tls.getRemoteCertificate(), timeout]);
      let der = certificate.data;
      if (der.length === 0) throw new Error('对方未提供证书');
      if (der[0] !== 0x30) {
        const pem = decode(der).replace(/-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s/g, '');
        der = new util.Base64Helper().decodeSync(pem);
      }
      const fingerprint = await Promise.race([digestBytes(der), timeout]);
      if (fingerprint !== peer.fingerprint) throw new Error('对方证书已变化，请重新核对并配对');
      if (settled) throw new Error('对方在请求前已关闭或中断连接');
      authenticated = true;
      let header = `POST ${path} HTTP/1.1\r\nHost: ${peer.host}:${peer.port}\r\nContent-Type: application/json\r\nContent-Length: ${utf8Length(body)}\r\nConnection: close\r\n`;
      if (key !== '') {
        if (!/^[A-Za-z0-9_-]{32,128}$/.test(key) || !/^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/.test(deviceId)) throw new Error('本机配对凭据不正确');
        header += `Authorization: Bearer ${key}\r\nX-Si-Device: ${deviceId}\r\n`;
      }
      await Promise.race([tls.send(encode(header + '\r\n' + body).buffer as ArrayBuffer), timeout]);
      return await Promise.race([response, timeout]);
    } finally {
      clearTimeout(timer);
      try { await tls.close(); } catch (_error) {}
    }
  }
}
