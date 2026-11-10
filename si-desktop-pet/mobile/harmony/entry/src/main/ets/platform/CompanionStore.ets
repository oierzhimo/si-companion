import preferences from '@ohos.data.preferences';
import common from '@ohos.app.ability.common';
import util from '@ohos.util';
import { Ledger, Document, Summary, JournalRow, Peer, parseWire, pairWith, exchangeWith } from '../core/Wire';
import { NativeTls, sha256, decode } from './NativeTls';
import { readLedger, writeLedger } from './NativeLedgerFile';

export class CompanionStore {
  private prefs: preferences.Preferences | undefined = undefined;
  private ledger: Ledger | undefined = undefined;
  private loading: Promise<void> | undefined = undefined;
  private peer: Peer | undefined = undefined;
  private lines: Record<string, string[]> = {};
  private bags: Map<string, string[]> = new Map<string, string[]>();
  private lastLine: Map<string, string> = new Map<string, string>();
  private networkBusy = false;
  private lastPat = -1000;
  private lastFeed = -20000;
  private transport = new NativeTls();
  message = '管理员，我在这里。';
  quiet = false;

  initialize(context: common.UIAbilityContext): Promise<void> {
    if (this.loading === undefined) this.loading = this.load(context);
    return this.loading;
  }
  private async load(context: common.UIAbilityContext): Promise<void> {
    this.prefs = await preferences.getPreferences(context, { name: 'si_companion' });
    const path = context.filesDir + '/ledger.json';
    const saved = await readLedger(path);
    const identity = saved === '' ? util.generateRandomUUID(true) : parseWire<Document>(saved).device_id;
    this.ledger = new Ledger(identity, sha256, async (text: string): Promise<void> => {
      await writeLedger(path, text);
    }, saved);
    await this.ledger.initialize();
    this.quiet = await this.prefs.get('quiet', false) as boolean;
    const connection = await this.prefs.get('peer', '') as string;
    if (connection !== '') this.peer = parseWire<Peer>(connection);
    this.lines = JSON.parse(decode(await context.resourceManager.getRawFileContent('lines.json'))) as Record<string, string[]>;
  }
  private line(kind: string): string {
    const options: string[] | undefined = this.lines[kind];
    if (options === undefined || options.length === 0) return '';
    if (options.length === 1) return options[0];
    let bag: string[] | undefined = this.bags.get(kind);
    if (bag === undefined || bag.length === 0) {
      const shuffled: string[] = [];
      for (let index = 0; index < options.length; index++) shuffled.push(options[index]);
      for (let index = shuffled.length - 1; index > 0; index--) {
        const swap = Math.floor(Math.random() * (index + 1));
        const held = shuffled[index];shuffled[index] = shuffled[swap];shuffled[swap] = held;
      }
      const last = this.lastLine.get(kind);
      if (last !== undefined && shuffled[shuffled.length - 1] === last) {
        const swap = Math.floor(Math.random() * (shuffled.length - 1));
        const held = shuffled[shuffled.length - 1];
        shuffled[shuffled.length - 1] = shuffled[swap];shuffled[swap] = held;
      }
      bag = shuffled;
      this.bags.set(kind, shuffled);
    }
    const text = bag[bag.length - 1];
    bag.splice(bag.length - 1, 1);
    this.lastLine.set(kind, text);
    return text;
  }
  summary(): Summary { return this.ledger!.summary(); }
  journal(): JournalRow[] { return this.ledger!.journal(); }
  peerName(): string { return this.peer === undefined ? '尚未连接电脑' : this.peer.name; }
  async action(kind: string): Promise<void> {
    const now = Date.now();
    if (kind === 'pat') { if (now >= this.lastPat && now - this.lastPat < 1000) return;this.lastPat = now; }
    else if (kind === 'snack') { if (now >= this.lastFeed && now - this.lastFeed < 20000) { this.message = '慢一点，先让我尝完这一口。';return; }this.lastFeed = now; }
    await this.ledger!.add(kind, {});
    await this.ledger!.add('journal', { text: kind === 'pat' ? '你在平板上摸了摸她的头' : '你在平板上送了一份点心' });
    this.message = this.line(kind === 'pat' ? 'pat' : 'feed');
  }
  async contact(kind: string): Promise<void> {
    if (kind === 'quiet') await this.setQuiet(true);
    this.message = this.line(kind);await this.ledger!.add('journal', { text: this.message });
  }
  async setQuiet(value: boolean): Promise<void> {
    await this.prefs!.put('quiet', value);await this.prefs!.flush();this.quiet = value;
    this.message = value ? '嗯，我就在边上。你专心。' : '我在这里，管理员。';
  }
  async pair(text: string): Promise<void> {
    if (this.networkBusy) throw new Error('正在处理上一项连接，请稍候');
    this.networkBusy = true;
    try {
      const peer = await pairWith(text, this.ledger!.deviceId, '祀的鸿蒙平板', this.transport);
      await this.prefs!.put('peer', JSON.stringify(peer));await this.prefs!.flush();this.peer = peer;
      this.message = `已连接 ${peer.name}，可以同步记录了。`;
    } finally { this.networkBusy = false; }
  }
  async sync(): Promise<void> {
    if (this.peer === undefined) throw new Error('先粘贴电脑端邀请进行配对');
    if (this.networkBusy) return;
    this.networkBusy = true;
    try { const count = await exchangeWith(this.peer, this.ledger!, this.transport);this.message = `同步完成，带回 ${count} 条新记录。`; }
    finally { this.networkBusy = false; }
  }
  async forget(): Promise<void> { await this.prefs!.put('peer', '');await this.prefs!.flush();this.peer = undefined;this.message = '已移除本机连接。'; }
  exportBundle(): Promise<string> { return this.ledger!.exportBundle(); }
  importBundle(text: string): Promise<number> { return this.ledger!.importBundle(text); }
}
export const companion = new CompanionStore();
