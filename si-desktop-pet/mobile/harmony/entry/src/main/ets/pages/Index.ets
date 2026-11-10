import common from '@ohos.app.ability.common';
import { companion } from '../platform/CompanionStore';
import { exportRecords, importRecords } from '../platform/RecordFiles';
import { JournalRow } from '../core/Wire';

@Entry
@Component
struct Index {
  @State ready: boolean = false;
  @State wide: boolean = false;
  @State message: string = '正在读取本机相伴记录…';
  @State affinity: number = 0;
  @State pats: number = 0;
  @State snacks: number = 0;
  @State quiet: boolean = false;
  @State peer: string = '尚未连接电脑';
  @State invitation: string = '';
  @State automatic: boolean = false;
  @State showJournal: boolean = false;
  @State journal: JournalRow[] = [];
  @State pose: string = 'idle';
  @State frame: number = 0;
  private context: common.UIAbilityContext | undefined = undefined;
  private blinkTimer: number = 0;
  private frameTimer: number = 0;
  private syncTimer: number = 0;
  private active: boolean = false;
  private idle: Resource[] = [$rawfile('hd/idle/00.png'),$rawfile('hd/idle/01.png'),$rawfile('hd/idle/02.png'),$rawfile('hd/idle/03.png'),$rawfile('hd/idle/04.png'),$rawfile('hd/idle/05.png')];
  private wave: Resource[] = [$rawfile('hd/waving/00.png'),$rawfile('hd/waving/01.png'),$rawfile('hd/waving/02.png'),$rawfile('hd/waving/03.png')];

  async aboutToAppear(): Promise<void> {
    this.active = true;
    try {
      this.context = getContext(this) as common.UIAbilityContext;
      await companion.initialize(this.context);this.refresh();this.ready = true;this.scheduleBlink();
    } catch (_error) { this.message = '暂时无法读取记录，请保留应用数据后重启检查。'; }
  }
  aboutToDisappear(): void { this.active = false;clearTimeout(this.blinkTimer);clearTimeout(this.frameTimer);clearInterval(this.syncTimer); }
  private refresh(): void {
    const summary = companion.summary();this.affinity = summary.affinity;this.pats = summary.pats;this.snacks = summary.snacks;
    this.message = companion.message;this.quiet = companion.quiet;this.peer = companion.peerName();this.journal = companion.journal();
  }
  private async task(operation: () => Promise<void>): Promise<void> {
    try { await operation();this.refresh(); }
    catch (_error) { this.message = '这次未能完成。请检查网络或记录格式，原有记录会保留。'; }
  }
  private image(): Resource { return this.quiet ? $rawfile('hd/sitting/00.png') : (this.pose === 'waving' ? this.wave[this.frame] : this.idle[this.frame]); }
  private play(pose: string): void {
    if (this.quiet || !this.active) return;
    clearTimeout(this.frameTimer);this.pose = pose;this.frame = 0;this.nextFrame();
  }
  private nextFrame(): void {
    const timings: number[] = this.pose === 'waving' ? [140,140,140,280] : [280,110,110,140,140,320];
    this.frameTimer = setTimeout(() => {
      if (!this.active) return;
      if (this.frame + 1 >= timings.length) { this.frame = 0;this.pose = 'idle';return; }
      this.frame++;this.nextFrame();
    }, timings[this.frame]);
  }
  private scheduleBlink(): void {
    this.blinkTimer = setTimeout(() => { if (this.active) { this.play('idle');this.scheduleBlink(); } }, 15000 + Math.floor(Math.random() * 15000));
  }
  private act(kind: string): void { this.play('waving');this.task(async (): Promise<void> => { await companion.action(kind); }); }
  private setAutomatic(value: boolean): void {
    this.automatic = value;clearInterval(this.syncTimer);
    if (value) this.syncTimer = setInterval(() => { if (this.active) this.task(async (): Promise<void> => { await companion.sync(); }); }, 60000);
  }
  @Builder portrait() {
    Column({ space: 16 }) {
      Image(this.image()).width('100%').height(this.wide ? 380 : 240).objectFit(ImageFit.Contain).onClick(() => { this.act('pat'); })
      Text(this.message).fontSize(18).fontColor('#254b3b').width('100%')
      Text(`亲密度 ${this.affinity} · 摸头 ${this.pats} 次 · 点心 ${this.snacks} 份`).fontSize(14).fontColor('#657d6b')
    }.padding(20).backgroundColor('#e0e9d8').borderRadius(20).width(this.wide ? '38%' : '100%')
  }
  @Builder interactions() {
    Column({ space: 12 }) {
      Button('摸摸头').width('100%').onClick(() => { this.act('pat'); })
      Button('送一份点心').width('100%').onClick(() => { this.act('snack'); })
      Select([{value:'想做点新东西'},{value:'有点卡住了'},{value:'想休息一会儿'},{value:'想听你聊画'},{value:'先安静陪我'}])
        .value('和祀说一句').width('100%').onSelect((index: number) => {
          const kinds: string[] = ['create','stuck','tired','painting','quiet'];
          this.task(async (): Promise<void> => { await companion.contact(kinds[index]); });
        })
      Row({space:12}) { Text('先安静陪我');Toggle({type:ToggleType.Switch,isOn:this.quiet}).onChange((value:boolean) => { if(value!==this.quiet)this.task(async (): Promise<void> => { await companion.setQuiet(value); }); }) }.width('100%').justifyContent(FlexAlign.SpaceBetween)
      Button(this.showJournal ? '收起相伴手记' : '看看相伴手记').width('100%').onClick(() => { this.showJournal = !this.showJournal; })
      if (this.showJournal) {
        ForEach(this.journal, (row: JournalRow) => { Column({space:4}) { Text(row.at).fontSize(12).fontColor('#81917e');Text(row.text).fontSize(15) }.width('100%').alignItems(HorizontalAlign.Start) })
      }
    }.width(this.wide ? '58%' : '100%')
  }
  build() {
    Scroll() {
      Column({ space: 20 }) {
        Text('管理员，今天也一起吧。').fontSize(30).fontWeight(FontWeight.Bold).fontColor('#254b3b').width('100%')
        Text('你认真做事，她自在停留。').fontSize(15).fontColor('#7a8c80').width('100%')
        if (!this.ready) { Text(this.message).fontSize(18) }
        else {
          if (this.wide) { Row() { this.portrait();this.interactions(); }.width('100%').justifyContent(FlexAlign.SpaceBetween).alignItems(VerticalAlign.Top) }
          else { this.portrait();this.interactions(); }
          Text('把相伴记录带过来').fontSize(23).fontWeight(FontWeight.Medium).width('100%')
          Text(this.peer).fontSize(15).fontColor('#657d6b').width('100%')
          TextArea({placeholder:'粘贴电脑端的配对邀请',text:this.invitation}).height(100).onChange((value:string) => {this.invitation=value;})
          Button('连接我的电脑').width('100%').onClick(() => {
            const invitation=this.invitation.trim();this.invitation='';
            this.task(async (): Promise<void> => {await companion.pair(invitation);});
          })
          Button('现在同步').width('100%').onClick(() => {this.task(async (): Promise<void> => {await companion.sync();});})
          Row({space:12}) { Text('应用打开期间，每分钟同步').layoutWeight(1);Toggle({type:ToggleType.Switch,isOn:this.automatic}).onChange((value:boolean) => {this.setAutomatic(value);}) }.width('100%')
          Button('移除本机连接').width('100%').onClick(() => {this.task(async (): Promise<void> => {await companion.forget();});})
          Button('导出记录包').width('100%').onClick(() => {this.task(async (): Promise<void> => {await exportRecords(this.context!,companion);});})
          Button('导入记录包').width('100%').onClick(() => {this.task(async (): Promise<void> => {await importRecords(this.context!,companion);});})
          Text('原生工程预览：当前仍用旧角色图集。悬浮、语音和专注功能尚待接入与设备验证。').fontSize(13).fontColor('#7a8c80').width('100%')
        }
      }.width('100%').constraintSize({maxWidth:1100}).padding(24)
    }.width('100%').height('100%').backgroundColor('#eef3ee').scrollBar(BarState.Auto)
    .onAreaChange((_old:Area,area:Area) => {this.wide=Number.parseFloat(area.width.toString())>760;})
  }
}
