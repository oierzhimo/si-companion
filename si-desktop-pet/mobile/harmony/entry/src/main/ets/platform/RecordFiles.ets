import common from '@ohos.app.ability.common';
import picker from '@ohos.file.picker';
import fs from '@ohos.file.fs';
import { MAX_BYTES } from '../core/Wire';
import { decode, encode } from './NativeTls';
import { CompanionStore } from './CompanionStore';

export async function exportRecords(context: common.UIAbilityContext, store: CompanionStore): Promise<void> {
  const options = new picker.DocumentSaveOptions();options.newFileNames = ['祀-平板记录.json'];
  const uris = await new picker.DocumentViewPicker(context).save(options);
  if (uris.length === 0) return;
  const text = await store.exportBundle();
  const file = fs.openSync(uris[0], fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
  try {
    const bytes = encode(text);let offset = 0;
    while (offset < bytes.length) {
      const rest = bytes.slice(offset);
      const written = fs.writeSync(file.fd, rest.buffer as ArrayBuffer, { offset: offset, length: rest.length });
      if (written <= 0) throw new Error('Incomplete record write');
      offset += written;
    }
    fs.fsyncSync(file.fd);
  } finally { fs.closeSync(file); }
  store.message = '记录包已导出。';
}
export async function importRecords(context: common.UIAbilityContext, store: CompanionStore): Promise<void> {
  const options = new picker.DocumentSelectOptions();options.maxSelectNumber = 1;
  const uris = await new picker.DocumentViewPicker(context).select(options);
  if (uris.length === 0) return;
  const file = fs.openSync(uris[0], fs.OpenMode.READ_ONLY);
  try {
    const size = fs.statSync(file.fd).size;
    if (size < 1 || size > MAX_BYTES) throw new Error('Invalid bundle size');
    const bytes = new Uint8Array(size);let offset = 0;
    while (offset < size) {
      const rest = new Uint8Array(size - offset);
      const read = fs.readSync(file.fd, rest.buffer as ArrayBuffer, { offset: offset, length: rest.length });
      if (read <= 0) throw new Error('Incomplete record file');
      bytes.set(rest.subarray(0, read), offset);offset += read;
    }
    const count = await store.importBundle(decode(bytes));store.message = `已合并 ${count} 条新记录。`;
  } finally { fs.closeSync(file); }
}
