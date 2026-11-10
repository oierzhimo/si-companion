import fs from '@ohos.file.fs';
import { MAX_BYTES } from '../core/Wire';
import { encode, decode } from './NativeTls';

/** Preferences values are bounded; growing event history belongs in a private file. */
export async function readLedger(path: string): Promise<string> {
  if (!fs.accessSync(path)) return '';
  const file = await fs.open(path, fs.OpenMode.READ_ONLY);
  try {
    const size = (await fs.stat(file.fd)).size;
    if (size < 1 || size > MAX_BYTES) throw new Error('Invalid ledger size');
    const data = new Uint8Array(size);let offset = 0;
    while (offset < size) {
      const rest = new Uint8Array(size - offset), read = await fs.read(file.fd, rest.buffer as ArrayBuffer, { offset: offset, length: rest.length });
      if (read <= 0) throw new Error('Incomplete ledger read');
      data.set(rest.subarray(0, read), offset);offset += read;
    }
    return decode(data);
  } finally { await fs.close(file); }
}
export async function writeLedger(path: string, text: string): Promise<void> {
  const data = encode(text);
  if (data.length > MAX_BYTES) throw new Error('Ledger too large');
  const temporary = path + '.tmp';
  const file = await fs.open(temporary, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
  try {
    let offset = 0;
    while (offset < data.length) {
      const rest = data.slice(offset), written = await fs.write(file.fd, rest.buffer as ArrayBuffer, { offset: offset, length: rest.length });
      if (written <= 0) throw new Error('Incomplete ledger write');
      offset += written;
    }
    await fs.fsync(file.fd);
  } finally { await fs.close(file); }
  await fs.rename(temporary, path);
}
