/** Platform-independent protocol code. Native adapters supply hashing and durable storage. */
export interface JournalRow { at: string; text: string; }
export interface Payload {
  text?: string; minutes?: number; key?: string; value?: string;
  pats?: number; snacks?: number; focus_sessions?: number; affinity?: number; history?: JournalRow[];
}
export interface WireEvent {
  protocol: number; event_id: string; device_id: string; sequence: number; lamport: number;
  kind: string; payload: Payload; occurred_at: string;
}
export interface Bundle { protocol: number; source_device: string; events: WireEvent[]; sha256: string; }
export interface Document { protocol: number; device_id: string; sequence: number; lamport: number; events: WireEvent[]; }
export interface Summary { pats: number; snacks: number; focus_sessions: number; affinity: number; }
/** Only keys that received a valid pref_set event are present; '' is a deliberate value. */
export interface SharedPreferences { preferred_name?: string; greeting_style?: string; }
interface PreferenceCandidate { lamport: number; deviceId: string; eventId: string; value: string; }
export interface Invitation {
  transport: number; host: string; port: number; fingerprint: string;
  device_id: string; name: string; invitation: string;
}
export interface Peer {
  host: string; port: number; fingerprint: string; device_id: string; name: string; key: string;
}
export interface HttpReply { status: number; body: string; }
export interface PairReply { transport: number; device_id: string; name: string; key: string; }
export interface ErrorReply { error: string; }
export interface Transport {
  request(peer: Invitation | Peer, path: string, body: string, deviceId?: string, key?: string): Promise<HttpReply>;
}
export type Hash = (value: string) => Promise<string>;
export type Persist = (document: string) => Promise<void>;
export const MAX_BYTES = 5000000;
export const MAX_EVENTS = 10000;

function check(value: boolean, message: string): void { if (!value) throw new Error(message); }
function fields(value: object, expected: string[]): void {
  check(value !== null && typeof value === 'object' && !Array.isArray(value), 'Invalid JSON object');
  check(Object.keys(value).sort().join(',') === expected.slice().sort().join(','), 'Invalid object fields');
}
function whole(value: number): void { check(Number.isSafeInteger(value) && value >= 0, 'Unsupported integer range'); }
function str(value: string, maximum: number): void {
  check(typeof value === 'string', 'Expected string');
  let count = 0;
  for (let i = 0; i < value.length; i++, count++) {
    const c = value.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      check(i + 1 < value.length, 'Invalid Unicode');
      const low = value.charCodeAt(++i);
      check(low >= 0xdc00 && low <= 0xdfff, 'Invalid Unicode');
    } else check(c < 0xdc00 || c > 0xdfff, 'Invalid Unicode');
  }
  check(count <= maximum, 'String too long');
}
function device(value: string): void { str(value, 64);check(/^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/.test(value), 'Invalid device ID'); }
function q(value: string): string { str(value, 10000);return JSON.stringify(value); }
export function utf8Length(text: string): number {
  str(text, MAX_BYTES);let size = 0;
  for (let i = 0; i < text.length; i++) {
    const value = text.charCodeAt(i);
    if (value < 0x80) size++;else if (value < 0x800) size += 2;
    else if (value >= 0xd800 && value <= 0xdbff) { size += 4;i++; } else size += 3;
  }
  return size;
}
/** JSON.parse loses the distinction between 1 and 1.0. Protocol numbers are integer tokens. */
export function parseWire<T>(text: string): T {
  check(utf8Length(text) <= MAX_BYTES, 'Bundle too large');
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inString) { if (c === '\\') i++;else if (c === '"') inString = false;continue; }
    if (c === '"') { inString = true;continue; }
    if (c === '-' || (c >= '0' && c <= '9')) {
      const start = i;
      while (i + 1 < text.length && /[0-9.eE+\-]/.test(text[i + 1])) i++;
      check(/^-?(0|[1-9][0-9]*)$/.test(text.slice(start, i + 1)), 'Expected integer token');
    }
  }
  return JSON.parse(text) as T;
}

function timestamp(value: string): void {
  str(value, 40);
  check(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+\-]\d\d:\d\d)$/.test(value), 'Invalid timestamp');
  const year = Number(value.slice(0, 4)), month = Number(value.slice(5, 7)), day = Number(value.slice(8, 10));
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  check(year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1], 'Invalid date');
  check(Number(value.slice(11, 13)) <= 23 && Number(value.slice(14, 16)) <= 59 && Number(value.slice(17, 19)) <= 59 && Number.isFinite(Date.parse(value)), 'Invalid time');
}

export function validateEvent(event: WireEvent): void {
  fields(event, ['protocol','event_id','device_id','sequence','lamport','kind','payload','occurred_at']);
  whole(event.protocol);check(event.protocol === 1, 'Unsupported protocol');
  device(event.device_id);whole(event.sequence);whole(event.lamport);
  check(event.sequence > 0 && event.event_id === `${event.device_id}:${event.sequence}`, 'Invalid event ID');
  timestamp(event.occurred_at);str(event.kind, 40);
  const p = event.payload;
  switch (event.kind) {
    case 'pat':case 'snack':fields(p, []);break;
    case 'journal':fields(p, ['text']);str(p.text!, 500);check(p.text!.length > 0, 'Empty journal');break;
    case 'focus_completed':fields(p, ['minutes']);whole(p.minutes!);check(p.minutes! >= 1 && p.minutes! <= 120, 'Invalid focus duration');break;
    case 'pref_set':fields(p, ['key','value']);check(p.key === 'preferred_name' || p.key === 'greeting_style', 'Unsupported preference');str(p.value!, 80);break;
    case 'legacy_snapshot':
      fields(p, ['pats','snacks','focus_sessions','affinity','history']);
      whole(p.pats!);whole(p.snacks!);whole(p.focus_sessions!);whole(p.affinity!);
      check(Array.isArray(p.history) && p.history!.length <= 100, 'Invalid legacy history');
      p.history!.forEach((row: JournalRow) => { fields(row, ['at','text']);str(row.at, 40);str(row.text, 500); });break;
    default:throw new Error('Unknown event kind');
  }
  check(utf8Length(canonicalEvent(event)) <= 32000, 'Event too large');
}
function canonicalPayload(event: WireEvent): string {
  const p = event.payload;
  switch(event.kind) {
    case 'pat':case 'snack':return '{}';
    case 'journal':return `{"text":${q(p.text!)}}`;
    case 'focus_completed':return `{"minutes":${p.minutes}}`;
    case 'pref_set':return `{"key":${q(p.key!)},"value":${q(p.value!)}}`;
    default:
      const history = p.history!.map((row: JournalRow) => `{"at":${q(row.at)},"text":${q(row.text)}}`).join(',');
      return `{"affinity":${p.affinity},"focus_sessions":${p.focus_sessions},"history":[${history}],"pats":${p.pats},"snacks":${p.snacks}}`;
  }
}
export function canonicalEvent(e: WireEvent): string {
  return `{"device_id":${q(e.device_id)},"event_id":${q(e.event_id)},"kind":${q(e.kind)},"lamport":${e.lamport},"occurred_at":${q(e.occurred_at)},"payload":${canonicalPayload(e)},"protocol":${e.protocol},"sequence":${e.sequence}}`;
}
function canonicalBody(source: string, rows: WireEvent[]): string {
  return `{"events":[${rows.map(canonicalEvent).join(',')}],"protocol":1,"source_device":${q(source)}}`;
}
function canonicalDocument(doc: Document): string {
  return `{"device_id":${q(doc.device_id)},"events":[${doc.events.map(canonicalEvent).join(',')}],"lamport":${doc.lamport},"protocol":1,"sequence":${doc.sequence}}`;
}
function baseline(rows: WireEvent[]): void { check(rows.filter((e: WireEvent) => e.kind === 'legacy_snapshot').length <= 1, 'Conflicting legacy baselines; manual migration required'); }
function summarize(rows: WireEvent[]): Summary {
  const result: Summary = { pats: 0, snacks: 0, focus_sessions: 0, affinity: 0 };
  rows.forEach((event: WireEvent) => {
    const p = event.payload;
    if (event.kind === 'legacy_snapshot') { result.pats += p.pats!;result.snacks += p.snacks!;result.focus_sessions += p.focus_sessions!;result.affinity += p.affinity!; }
    if (event.kind === 'pat') { result.pats++;result.affinity++; }
    if (event.kind === 'snack') { result.snacks++;result.affinity += 3; }
    if (event.kind === 'focus_completed') { result.focus_sessions++;result.affinity += 5; }
  });
  Object.values(result).forEach(whole);return result;
}
function journalOrder(at: string): string {
  const fraction = /\.(\d+)/.exec(at);
  const micros = ((fraction === null ? '' : fraction[1]) + '000000').slice(0, 6);
  const seconds = Date.parse(at) - Number(micros.slice(0, 3));
  return new Date(seconds).toISOString().slice(0, 19) + '.' + micros + 'Z';
}
function lexical(a: string, b: string): number { return a < b ? -1 : (a > b ? 1 : 0); }
/** Python compares strings by Unicode code point; JavaScript compares UTF-16 units. */
function codePointOrder(a: string, b: string): number {
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    const left = a.codePointAt(i)!, right = b.codePointAt(j)!;
    if (left !== right) return left < right ? -1 : 1;
    i += left > 0xffff ? 2 : 1;j += right > 0xffff ? 2 : 1;
  }
  return (a.length - i) - (b.length - j);
}
/** Tuple order used by Python summary(): (lamport, device_id, event_id, value). */
function preferenceOrder(a: PreferenceCandidate, b: PreferenceCandidate): number {
  if (a.lamport !== b.lamport) return a.lamport < b.lamport ? -1 : 1;
  const device = codePointOrder(a.deviceId, b.deviceId);if (device !== 0) return device;
  const event = codePointOrder(a.eventId, b.eventId);if (event !== 0) return event;
  return codePointOrder(a.value, b.value);
}

export class Ledger {
  readonly deviceId: string;
  private sequence = 0;
  private lamport = 0;
  private events = new Map<string, WireEvent>();
  private tail: Promise<void> = Promise.resolve();
  private hash: Hash;
  private persist: Persist;

  constructor(deviceId: string, hash: Hash, persist: Persist, saved: string = '') {
    device(deviceId);this.deviceId = deviceId;this.hash = hash;this.persist = persist;
    if (saved !== '') {
      const doc = parseWire<Document>(saved);fields(doc, ['protocol','device_id','sequence','lamport','events']);
      check(doc.protocol === 1 && doc.device_id === deviceId, 'Invalid local ledger');
      whole(doc.sequence);whole(doc.lamport);check(Array.isArray(doc.events) && doc.events.length <= MAX_EVENTS, 'Invalid ledger');
      doc.events.forEach((event: WireEvent) => {
        validateEvent(event);
        const old = this.events.get(event.event_id);
        check(old === undefined || canonicalEvent(old) === canonicalEvent(event), 'Conflicting event ID');
        this.events.set(event.event_id, event);
        check(event.device_id !== deviceId || event.sequence <= doc.sequence, 'Sequence went backwards');
        this.lamport = Math.max(this.lamport, event.lamport);
      });
      baseline(doc.events);this.sequence = doc.sequence;this.lamport = Math.max(this.lamport, doc.lamport);
    }
  }
  private locked<T>(work: () => Promise<T>): Promise<T> {
    const job = this.tail.then(work);this.tail = job.then(() => {}, () => {});return job;
  }
  private rows(map: Map<string, WireEvent> = this.events): WireEvent[] {
    return Array.from(map.keys()).sort().map((key: string) => map.get(key)!);
  }
  private async commit(next: Map<string, WireEvent>, sequence: number, lamport: number): Promise<void> {
    whole(sequence);whole(lamport);
    summarize(this.rows(next));
    const document: Document = { protocol: 1, device_id: this.deviceId, sequence: sequence, lamport: lamport, events: this.rows(next) };
    const text = canonicalDocument(document);check(utf8Length(text) <= MAX_BYTES, 'Ledger too large');
    await this.persist(text);this.events = next;this.sequence = sequence;this.lamport = lamport;
  }
  initialize(): Promise<void> { return this.locked(() => this.commit(this.events, this.sequence, this.lamport)); }
  add(kind: string, payload: Payload, at: string = new Date().toISOString()): Promise<void> {
    return this.locked(async () => {
      check(kind !== 'legacy_snapshot' && this.events.size < MAX_EVENTS, 'Cannot add event');
      const event: WireEvent = { protocol: 1, event_id: `${this.deviceId}:${this.sequence + 1}`, device_id: this.deviceId,
        sequence: this.sequence + 1, lamport: this.lamport + 1, kind: kind,
        payload: JSON.parse(JSON.stringify(payload)) as Payload, occurred_at: at };
      validateEvent(event);const next = new Map(this.events);next.set(event.event_id, event);
      await this.commit(next, event.sequence, event.lamport);
    });
  }
  exportBundle(): Promise<string> {
    return this.locked(async () => {
      const rows = this.rows(), checksum = await this.hash(canonicalBody(this.deviceId, rows));
      const result = `{"events":[${rows.map(canonicalEvent).join(',')}],"protocol":1,"sha256":${q(checksum)},"source_device":${q(this.deviceId)}}`;
      check(utf8Length(result) <= MAX_BYTES, 'Bundle too large');return result;
    });
  }
  importBundle(text: string): Promise<number> {
    return this.locked(async () => {
      const body = parseWire<Bundle>(text);fields(body, ['protocol','source_device','events','sha256']);
      check(body.protocol === 1, 'Unsupported bundle protocol');device(body.source_device);
      check(Array.isArray(body.events) && body.events.length <= MAX_EVENTS, 'Invalid event batch');
      body.events.forEach(validateEvent);
      check(typeof body.sha256 === 'string' && body.sha256 === await this.hash(canonicalBody(body.source_device, body.events)), 'Bundle checksum mismatch');
      const next = new Map(this.events);let lamport = this.lamport;
      body.events.forEach((event: WireEvent) => {
        const old = next.get(event.event_id);
        check(old === undefined || canonicalEvent(old) === canonicalEvent(event), 'Conflicting event ID');
        next.set(event.event_id, event);lamport = Math.max(lamport, event.lamport);
      });
      baseline(this.rows(next));check(next.size <= MAX_EVENTS, 'Ledger full');
      const added = next.size - this.events.size;
      if (added > 0) await this.commit(next, this.sequence, lamport + 1);
      return added;
    });
  }
  summary(): Summary {
    return summarize(this.rows());
  }
  /**
   * Read-only projection of pref_set events, matching sync_v1.summary() preferences.
   * It never persists, sends, or applies anything; empty values stay as values.
   */
  sharedPreferences(): SharedPreferences {
    const winners = new Map<string, PreferenceCandidate>();
    this.events.forEach((event: WireEvent) => {
      if (event.kind !== 'pref_set') return;
      const key = event.payload.key;
      if (key !== 'preferred_name' && key !== 'greeting_style') return;
      const candidate: PreferenceCandidate = { lamport: event.lamport, deviceId: event.device_id,
        eventId: event.event_id, value: event.payload.value! };
      const old = winners.get(key);
      if (old === undefined || preferenceOrder(candidate, old) > 0) winners.set(key, candidate);
    });
    const result: SharedPreferences = {};
    const preferred = winners.get('preferred_name');
    const greeting = winners.get('greeting_style');
    if (preferred !== undefined) result.preferred_name = preferred.value;
    if (greeting !== undefined) result.greeting_style = greeting.value;
    return result;
  }
  journal(): JournalRow[] {
    const history: JournalRow[] = [], entries: WireEvent[] = [];
    this.events.forEach((event: WireEvent) => {
      if (event.kind === 'legacy_snapshot') history.push(...event.payload.history!);
      if (event.kind === 'journal') entries.push(event);
    });
    entries.sort((a: WireEvent, b: WireEvent) => lexical(journalOrder(a.occurred_at), journalOrder(b.occurred_at)) || lexical(a.event_id, b.event_id));
    return history.concat(entries.map((e: WireEvent) => ({ at: e.occurred_at, text: e.payload.text! }))).slice(-100);
  }
}

export function validateAddress(host: string): void {
  check(typeof host === 'string' && /^[0-9]{1,3}(\.[0-9]{1,3}){3}$/.test(host), '请输入局域网 IPv4 地址');
  const ip = host.split('.').map((s: string) => Number(s));
  check(ip.every((n: number) => n >= 0 && n <= 255) && ip.join('.') === host, '地址格式不正确');
  check(ip[0] === 10 || ip[0] === 127 || (ip[0] === 172 && ip[1] >= 16 && ip[1] <= 31) || (ip[0] === 192 && ip[1] === 168), '仅支持局域网地址');
}
export function parseInvitation(text: string): Invitation {
  check(utf8Length(text) <= 4096, '邀请过长');const peer = parseWire<Invitation>(text);
  fields(peer, ['transport','host','port','fingerprint','device_id','name','invitation']);
  check(peer.transport === 1, '不支持的配对版本');validateAddress(peer.host);whole(peer.port);
  check(peer.port >= 1 && peer.port <= 65535 && /^[0-9a-f]{64}$/.test(peer.fingerprint), '配对信息不正确');
  device(peer.device_id);str(peer.name, 60);check(peer.name.length > 0 && /^[A-Za-z0-9_-]{32,128}$/.test(peer.invitation), '邀请不正确');return peer;
}
export async function pairWith(text: string, deviceId: string, name: string, transport: Transport): Promise<Peer> {
  const invitation = parseInvitation(text);device(deviceId);str(name, 60);
  const body = `{"device_id":${q(deviceId)},"invitation":${q(invitation.invitation)},"name":${q(name)}}`;
  const response = await transport.request(invitation, '/v1/pair', body);
  if (response.status !== 200) throw new Error(parseWire<ErrorReply>(response.body).error);
  const answer = parseWire<PairReply>(response.body);
  check(answer.transport === 1 && answer.device_id === invitation.device_id && /^[A-Za-z0-9_-]{32,128}$/.test(answer.key), '对方配对响应不正确');
  return { host: invitation.host, port: invitation.port, fingerprint: invitation.fingerprint, device_id: invitation.device_id, name: invitation.name, key: answer.key };
}
export async function exchangeWith(peer: Peer, ledger: Ledger, transport: Transport): Promise<number> {
  const response = await transport.request(peer, '/v1/exchange', await ledger.exportBundle(), ledger.deviceId, peer.key);
  if (response.status !== 200) throw new Error(parseWire<ErrorReply>(response.body).error);
  check(parseWire<Bundle>(response.body).source_device === peer.device_id, '对方记录来源与配对信息不一致');
  return ledger.importBundle(response.body);
}
