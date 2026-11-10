import type { HttpReply } from './Wire';
import { MAX_BYTES } from './Wire';

/** Handles split TLS records without decoding partial UTF-8 sequences. */
export class HttpBuffer {
  private header = '';
  private body: Uint8Array | undefined = undefined;
  private written = 0;
  private status = 0;
  private done = false;
  private decode: (data: Uint8Array) => string;

  constructor(decode: (data: Uint8Array) => string) { this.decode = decode; }
  push(chunk: Uint8Array): HttpReply | undefined {
    if (this.done) throw new Error('Unexpected data after response');
    let offset = 0;
    if (this.body === undefined) {
      while (offset < chunk.length) {
        const byte = chunk[offset++];
        if (byte > 127 || this.header.length >= 16384) throw new Error('Invalid or oversized HTTP header');
        this.header += String.fromCharCode(byte);
        if (this.header.endsWith('\r\n\r\n')) { this.parseHeader();break; }
      }
    }
    if (this.body === undefined) return undefined;
    const rest = chunk.subarray(offset);
    if (this.written + rest.length > this.body.length) throw new Error('Unexpected response size');
    this.body.set(rest, this.written);this.written += rest.length;
    if (this.written !== this.body.length) return undefined;
    this.done = true;
    return { status: this.status, body: this.decode(this.body) };
  }
  private parseHeader(): void {
    const lines = this.header.split('\r\n');
    const first = /^HTTP\/1\.[01] ([0-9]{3})(?: |$)/.exec(lines[0]);
    if (first === null) throw new Error('Invalid HTTP status');
    this.status = Number(first[1]);let length = -1;
    for (let i = 1; i < lines.length - 2; i++) {
      const colon = lines[i].indexOf(':');
      if (colon <= 0) throw new Error('Invalid HTTP header');
      const name = lines[i].slice(0, colon).toLowerCase(), value = lines[i].slice(colon + 1).trim();
      if (name === 'transfer-encoding') throw new Error('Unsupported transfer encoding');
      if (name === 'content-length') {
        if (length >= 0 || !/^[0-9]+$/.test(value)) throw new Error('Invalid content length');
        length = Number(value);
      }
    }
    if (!Number.isSafeInteger(length) || length < 0 || length > MAX_BYTES) throw new Error('Response too large or missing length');
    this.body = new Uint8Array(length);
  }
}
