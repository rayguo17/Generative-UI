import { common } from '@kit.AbilityKit';
import { fileIo as fs } from '@kit.CoreFileKit';
import { picker } from '@kit.CoreFileKit';
import { promptAction } from '@kit.ArkUI';
import { webview } from '@kit.ArkWeb';

interface GeneratePayload {
  query: string;
  backendUrl: string;
  baseUrl?: string;
  apiKey?: string;
  model?: string;
}

interface WebReplySnapshot {
  status: string;
  height: number;
  hasContent: boolean;
}

@Entry
@Component
struct Index {
  private webCtrl: webview.WebviewController = new webview.WebviewController();
  private queryController: TextInputController = new TextInputController();
  private chatScroll: Scroller = new Scroller();
  private heightPollTimer: number = -1;
  private statusAnimTimer: number = -1;
  private readonly replyMinHeight: number = 1;
  private readonly replyMaxHeight: number = 760;
  private readonly defaultQuery: string = '五一去南京的旅游攻略';
  /** 会话区：首条 query 距滚动区顶部、生成 UI 距滚动区底部的留白（vp） */
  private readonly chatContentPadTop: number = 10;
  private readonly chatContentPadBottom: number = 10;
  /** 无会话欢迎区相对垂直居中再上移（vp），略偏上更协调 */
  private readonly welcomeBiasUpVp: number = 56;
  /** 实际发送的 query 超过该字数时，发送后外层滚到底，便于看到长气泡尾部与下方回复区 */
  private readonly longQueryOuterScrollBottomChars: number = 160;

  @State query: string = '';
  @State statusAnimTick: number = 0;
  @State backendUrl: string = 'http://127.0.0.1:8765';
  @State baseUrl: string = '';
  @State apiKey: string = '';
  @State model: string = '';
  @State status: string = '就绪';
  @State webReady: boolean = false;
  @State lastUserMessage: string = '';
  @State hasConversation: boolean = false;
  @State showSettings: boolean = false;
  @State draftBackendUrl: string = '';
  @State draftBaseUrl: string = '';
  @State draftApiKey: string = '';
  @State draftModel: string = '';
  @State settingsError: string = '';
  @State replyHeight: number = 1;
  @State replyStatusText: string = '';
  private pendingScript: string = '';
  /** H5 上报的 #root 内容高度（可大于 replyMaxHeight）；用于 Web 已封顶后仍随内部流式增高触发外层滚到底 */
  private lastWebReportedHeight: number = -1;

  /** Scroll 在首帧短内容时可能把视口滚到中间；发送后强制贴顶。 */
  private scrollChatToTop(): void {
    setTimeout((): void => {
      try {
        this.chatScroll.scrollEdge(Edge.Top);
      } catch {
        // Scroll 尚未完成布局时忽略
      }
    }, 32);
  }

  /** 仅当 Web 已达最大高度时跟外滚到底；短内容时滚底会把整块顶下去造成顶部留白。 */
  private scrollOuterChatToBottom(delayMs: number = 48): void {
    setTimeout((): void => {
      try {
        this.chatScroll.scrollEdge(Edge.Bottom);
      } catch {
        // 布局未完成时忽略
      }
    }, delayMs);
  }

  /** 发送后：短 query 仍贴顶；长 query 滚到底，避免整屏只看到气泡上半截（多等一帧便于长文本布局） */
  private scrollAfterSend(trimmedQuery: string): void {
    if (trimmedQuery.length > this.longQueryOuterScrollBottomChars) {
      this.scrollOuterChatToBottom(96);
    } else {
      this.scrollChatToTop();
    }
  }

  private startStatusAnim(): void {
    this.stopStatusAnim();
    this.statusAnimTick = 0;
    this.statusAnimTimer = setInterval((): void => {
      this.statusAnimTick = (this.statusAnimTick + 1) % 3;
    }, 420);
  }

  private stopStatusAnim(): void {
    if (this.statusAnimTimer !== -1) {
      clearInterval(this.statusAnimTimer);
      this.statusAnimTimer = -1;
    }
  }

  private isTerminalStatus(): boolean {
    const s: string = this.replyStatusText;
    return (
      s === '已完成.' ||
      s === '已停止' ||
      s.indexOf('失败') >= 0 ||
      s.indexOf('未识别') >= 0
    );
  }

  private getStatusLoadingBase(): string {
    const s: string = this.replyStatusText;
    if (s.indexOf('发送') >= 0) {
      return '发送中';
    }
    if (s.indexOf('生成') >= 0) {
      return '生成中';
    }
    if (s.indexOf('等待') >= 0) {
      return '等待生成';
    }
    return s.replace(/\.+$/, '');
  }

  private getAnimatedStatusLine(): string {
    if (this.isTerminalStatus()) {
      return this.replyStatusText;
    }
    const base: string = this.getStatusLoadingBase();
    const dots: string = '.'.repeat(1 + (this.statusAnimTick % 3));
    return base + dots;
  }

  /** 与 frontend/src/main.ts 中 buildCurrentHtmlDocument（预览 iframe 壳）一致；inner 为 #root 内 HTML 片段 */
  private toastExport(msg: string): void {
    try {
      promptAction.showToast({ message: msg, duration: 4000 });
    } catch {
      // Toast 不可用时忽略
    }
  }

  private wrapExportHtmlDocument(inner: string): string {
    const esc: string = inner.replace(/<\/script/gi, '<\\/script');
    const tailwindTag: string = '<scr' + 'ipt src="https://cdn.tailwindcss.com"></scr' + 'ipt>';
    const bridge: string =
      '</div><scr' +
      'ipt>' +
      '(() => { function parseInteractions(el) { if (!el) return null; const raw = el.getAttribute("data-interactions"); if (!raw) return null; try { return JSON.parse(raw); } catch { return null; } } ' +
      'document.addEventListener("click", (event) => { const target = event.target; if (!(target instanceof Element)) return; const node = target.closest("[data-interactions]"); if (!node) return; ' +
      'const interactions = parseInteractions(node); const onClick = interactions && Array.isArray(interactions.onClick) ? interactions.onClick : []; if (!onClick.length) return; event.preventDefault(); ' +
      'window.parent.postMessage({ source: "h5-stream-ui-bridge", type: "interaction", event: "onClick", actions: onClick }, "*"); }, true); })();' +
      '</scr' +
      'ipt></body></html>';
    return (
      '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>' +
      '<meta name="color-scheme" content="light"/>' +
      tailwindTag +
      '<style>html{color-scheme:light;}html,body{margin:0;padding:0;background:#fff;}#root{display:block;}</style></head><body><div id="root">' +
      esc +
      bridge
    );
  }

  /** ArkWeb 对 runJavaScript 返回值常做 JSON 包装；大字符串在页面内切成 __exportChunkParts 再逐段拉回 */
  private unwrapJsString(raw: Object | string | number | undefined): string {
    if (raw === undefined || raw === null) {
      return '';
    }
    if (typeof raw === 'number' && Number.isFinite(raw)) {
      return String(Math.trunc(raw));
    }
    let s: string = String(raw).trim();
    if (s.length >= 2 && s[0] === '"' && s[s.length - 1] === '"') {
      try {
        const parsed: string = JSON.parse(s) as string;
        if (typeof parsed === 'string') {
          return parsed;
        }
      } catch {
        s = s.slice(1, s.length - 1);
      }
    }
    return s;
  }

  private parseJsInt(raw: Object | undefined): number {
    const s: string = this.unwrapJsString(raw as Object);
    const n: number = Number.parseInt(s, 10);
    return Number.isFinite(n) ? n : 0;
  }

  private async readWebExportInnerHtml(): Promise<string> {
    const prepareJs: string =
      '(function(){try{var r=document.getElementById("root");var a=(typeof window.__exportHtmlFragment==="string"&&window.__exportHtmlFragment.length)?window.__exportHtmlFragment:(r?r.innerHTML:"");if(!a)return"0";window.__exportChunkParts=[];var CH=8000;for(var i=0;i<a.length;i+=CH){window.__exportChunkParts.push(a.slice(i,i+CH));}return String(window.__exportChunkParts.length);}catch(e){return "ERR:"+String(e.message||e)}})()';
    const lenRaw: Object = await this.webCtrl.runJavaScript(prepareJs);
    const lenHead: string = this.unwrapJsString(lenRaw);
    if (lenHead.indexOf('ERR:') === 0) {
      return '';
    }
    const chunkCount: number = this.parseJsInt(lenRaw);
    if (chunkCount <= 0) {
      return '';
    }
    let acc: string = '';
    for (let i: number = 0; i < chunkCount; i++) {
      const chunkJs: string = `(function(){try{var p=window.__exportChunkParts&&window.__exportChunkParts[${i}];return p!=null?String(p):"";}catch(e){return ""}})()`;
      const piece: Object = await this.webCtrl.runJavaScript(chunkJs);
      acc += this.unwrapJsString(piece);
    }
    void this.webCtrl.runJavaScript('try{delete window.__exportChunkParts}catch(e){}');
    return acc;
  }

  private async saveExportedHtmlSnapshot(): Promise<void> {
    if (!this.webReady) {
      this.status = 'Web 未就绪';
      this.toastExport('Web 未就绪，请稍后再试');
      return;
    }
    if (!this.hasConversation) {
      this.status = '请先生成内容后再导出';
      this.toastExport('请先生成内容后再点下载');
      return;
    }
    this.toastExport('正在导出 HTML…');
    try {
      const inner: string = await this.readWebExportInnerHtml();
      if (!inner || inner.trim().length < 5) {
        this.status = '暂无可导出的 HTML';
        this.toastExport('暂无可导出的 HTML（预览区可能为空）');
        return;
      }
      const html: string = this.wrapExportHtmlDocument(inner);
      const host = this.getUIContext().getHostContext();
      if (host == null) {
        this.status = '无法获取应用上下文';
        this.toastExport('无法获取应用上下文');
        return;
      }
      const ctx: common.UIAbilityContext = host as common.UIAbilityContext;
      if (html.length === 0) {
        this.status = '编码后内容为空';
        this.toastExport('编码后内容为空');
        return;
      }
      const internalPath: string = `${ctx.filesDir}/h5-export-last.html`;
      const internalFile = fs.openSync(
        internalPath,
        fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC
      );
      /* CoreFileKit：部分版本对 writeSync(fd, Uint8Array) 报 Invalid argument，字符串写入稳定 */
      fs.writeSync(internalFile.fd, html);
      fs.closeSync(internalFile);
      const shortPath: string =
        internalPath.length > 72 ? `…${internalPath.slice(-68)}` : internalPath;
      this.toastExport(`已写入应用沙箱：${shortPath}`);

      const saveOpts: picker.DocumentSaveOptions = new picker.DocumentSaveOptions();
      saveOpts.newFileNames = [`generated-ui-${Date.now()}.html`];
      const docPicker: picker.DocumentViewPicker = new picker.DocumentViewPicker(ctx);
      let savedPicker: boolean = false;
      try {
        const uris: Array<string> = await docPicker.save(saveOpts);
        if (uris && uris.length > 0) {
          const uri: string = uris[0];
          const file = fs.openSync(uri, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
          fs.writeSync(file.fd, html);
          fs.closeSync(file);
          savedPicker = true;
        }
      } catch (pickErr) {
        this.toastExport(
          `系统保存对话框失败（沙箱副本仍可用）：${String((pickErr as Error).message ?? pickErr)}`
        );
      }
      void this.webCtrl.runJavaScript(
        'try{delete window.__exportPullBuf;delete window.__exportChunkParts}catch(e){}'
      );
      this.status = savedPicker
        ? 'H5 已保存（自选位置 + 应用沙箱各一份）'
        : 'H5 已写入应用沙箱（h5-export-last.html，可用 hdc file recv 取出）';
      if (savedPicker) {
        this.toastExport('已同时写入您选择的位置');
      }
    } catch (e) {
      const msg: string = String((e as Error).message ?? e);
      this.status = `导出失败：${msg}`;
      this.toastExport(`导出失败：${msg}`);
    }
  }

  private parseWebSnapshot(result: Object): WebReplySnapshot | null {
    const raw: string = String(result ?? '').trim();
    if (!raw) {
      return null;
    }
    try {
      const firstPass = JSON.parse(raw) as Object | string;
      const payload: Object =
        typeof firstPass === 'string'
          ? JSON.parse(firstPass) as Object
          : firstPass;
      const snapshot = payload as WebReplySnapshot;
      return {
        status: typeof snapshot.status === 'string' ? snapshot.status : '',
        height: Number(snapshot.height ?? 0),
        hasContent: Boolean(snapshot.hasContent)
      };
    } catch {
      return null;
    }
  }

  private openSettings(): void {
    this.draftBackendUrl = this.backendUrl;
    this.draftBaseUrl = this.baseUrl;
    this.draftApiKey = this.apiKey;
    this.draftModel = this.model;
    this.settingsError = '';
    this.showSettings = true;
  }

  private closeSettings(): void {
    this.showSettings = false;
    this.settingsError = '';
  }

  private saveSettings(): void {
    if (!this.draftBackendUrl.trim()) {
      this.settingsError = 'BACKEND_URL 不能为空';
      return;
    }

    const hasBaseUrl: boolean = this.draftBaseUrl.trim().length > 0;
    const hasApiKey: boolean = this.draftApiKey.trim().length > 0;
    const hasModel: boolean = this.draftModel.trim().length > 0;

    this.backendUrl = this.draftBackendUrl.trim();
    this.baseUrl = hasBaseUrl ? this.draftBaseUrl.trim() : '';
    this.apiKey = hasApiKey ? this.draftApiKey.trim() : '';
    this.model = hasModel ? this.draftModel.trim() : '';
    this.status = '配置已保存';
    this.closeSettings();
  }

  private sendToWeb(): void {
    if (!this.backendUrl.trim()) {
      this.status = '请先填写 BACKEND_URL';
      return;
    }
    try {
      this.queryController.stopEditing();
    } catch {
      // 部分版本或尚未获焦时忽略
    }
    const raw: string = this.query.trim();
    const trimmedQuery: string = raw.length > 0 ? raw : this.defaultQuery;
    this.lastUserMessage = trimmedQuery;
    this.hasConversation = true;
    this.replyStatusText = '发送中';
    this.startStatusAnim();
    this.replyHeight = 0;
    this.lastWebReportedHeight = -1;
    const payload: GeneratePayload = {
      query: trimmedQuery,
      backendUrl: this.backendUrl.trim()
    };
    if (this.baseUrl.trim()) {
      payload.baseUrl = this.baseUrl.trim();
    }
    if (this.apiKey.trim()) {
      payload.apiKey = this.apiKey.trim();
    }
    if (this.model.trim()) {
      payload.model = this.model.trim();
    }
    const script = `window.runGenerate(${JSON.stringify(payload)});`;
    if (!this.webReady) {
      this.pendingScript = script;
      this.replyHeight = this.replyMinHeight;
      this.query = '';
      this.scrollAfterSend(trimmedQuery);
      return;
    }
    this.webCtrl.runJavaScript(script);
    this.replyHeight = this.replyMinHeight;
    this.query = '';
    this.scrollAfterSend(trimmedQuery);
  }

  private startReplyHeightPolling(): void {
    if (this.heightPollTimer !== -1) {
      clearInterval(this.heightPollTimer);
    }
    this.heightPollTimer = setInterval(() => {
      if (!this.hasConversation || !this.webReady) {
        return;
      }
      this.webCtrl.runJavaScript(
        `(() => {
          const state = window.__chatState || { status: "", height: 0, hasContent: false };
          return JSON.stringify(state);
        })()`
      ).then((result: Object) => {
        const snapshot: WebReplySnapshot | null = this.parseWebSnapshot(result);
        if (snapshot === null) {
          return;
        }
        const wasTerminal: boolean = this.isTerminalStatus();
        const nextStatus: string = snapshot.status ? snapshot.status.trim() : '';
        if (nextStatus.length > 0 && nextStatus !== this.replyStatusText) {
          this.replyStatusText = nextStatus;
        }
        if (this.isTerminalStatus()) {
          this.stopStatusAnim();
        }
        const nowTerminal: boolean = this.isTerminalStatus();
        const nextHeight: number = Number(snapshot.height);
        if (!Number.isFinite(nextHeight) || nextHeight < 0) {
          return;
        }
        const reportedH: number = Math.ceil(nextHeight);
        const prevReplyH: number = this.replyHeight;
        const clamped: number = snapshot.hasContent
          ? Math.max(this.replyMinHeight, Math.min(nextHeight, this.replyMaxHeight))
          : this.replyMinHeight;
        if (clamped !== this.replyHeight) {
          this.replyHeight = clamped;
        }
        const scrollForHeight: boolean =
          snapshot.hasContent &&
          (clamped !== prevReplyH ||
            (this.replyHeight >= this.replyMaxHeight && reportedH > this.lastWebReportedHeight));
        const scrollForTerminal: boolean = snapshot.hasContent && nowTerminal && !wasTerminal;
        if (
          this.hasConversation &&
          (scrollForHeight || scrollForTerminal)
        ) {
          if (this.replyHeight >= this.replyMaxHeight && reportedH > this.lastWebReportedHeight) {
            this.lastWebReportedHeight = reportedH;
          }
          this.scrollOuterChatToBottom();
        }
      }).catch(() => {});
    }, 180);
  }

  aboutToDisappear(): void {
    if (this.heightPollTimer !== -1) {
      clearInterval(this.heightPollTimer);
      this.heightPollTimer = -1;
    }
    this.stopStatusAnim();
  }

  build() {
    Stack({ alignContent: Alignment.TopStart }) {
      Column({ space: 10 }) {
        Row() {
          Column({ space: 2 }) {
            Text('小艺式聊天')
              .fontSize(20)
              .fontWeight(FontWeight.Bold)
            Text('输入您的问题，系统将以UI形式回复')
              .fontSize(12)
              .fontColor('#6b7280')
          }
          .alignItems(HorizontalAlign.Start)
          Blank()
          Button('模型设置')
            .fontSize(13)
            .backgroundColor('#ffffff')
            .fontColor('#111827')
            .borderRadius(18)
            .padding({ left: 14, right: 14 })
            .onClick(() => {
              this.openSettings();
            })
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 14 })

        Stack({ alignContent: Alignment.TopStart }) {
          Scroll(this.chatScroll) {
            // 无会话：中间区域垂直居中欢迎语。有会话：minHeight 100% + 底部 Blank 贴顶布局，避免 Scroll 把矮内容垂直居中造成「假留白」；长内容仍可滚。
            Column() {
              Column({ space: this.hasConversation ? 8 : 12 }) {
                if (!this.hasConversation) {
                  Column({ space: 16 }) {
                    Image($rawfile('huawei-celia-ball.webp'))
                      .width(80)
                      .height(80)
                      .objectFit(ImageFit.Cover)

                    Text('你好，我是你的 AI 助手')
                      .fontSize(20)
                      .fontWeight(FontWeight.Medium)
                      .fontColor('#111827')
                      .textAlign(TextAlign.Center)
                  }
                  .width('100%')
                  .alignItems(HorizontalAlign.Center)
                  .padding({ left: 18, right: 18, top: 8, bottom: 8 })
                  .offset({ y: -this.welcomeBiasUpVp })
                }

                if (this.hasConversation) {
                  Column({ space: 10 }) {
                    Row() {
                      Blank()
                      Text(this.lastUserMessage)
                        .fontSize(15)
                        .fontColor('#ffffff')
                        .padding({ left: 14, right: 14, top: 12, bottom: 12 })
                        .backgroundColor('#2563eb')
                        .borderRadius(20)
                        .constraintSize({ maxWidth: '82%' })
                    }
                    .width('100%')

                    if (this.replyStatusText.length > 0) {
                      Row({ space: 8 }) {
                        if (!this.isTerminalStatus()) {
                          LoadingProgress()
                            .width(14)
                            .height(14)
                            .color('#6b7280')
                        }
                        Text(this.getAnimatedStatusLine())
                          .fontSize(12)
                          .fontColor(this.replyStatusText === '已完成.' ? '#16a34a' : '#6b7280')
                        if (this.replyStatusText === '已完成.') {
                          Button({ type: ButtonType.Circle }) {
                            Text('↓')
                              .fontSize(16)
                              .fontColor(this.webReady ? '#111827' : '#9ca3af')
                          }
                          .width(32)
                          .height(32)
                          .backgroundColor('#ffffff')
                          .enabled(this.webReady)
                          .onClick(() => {
                            void this.saveExportedHtmlSnapshot().catch((err: Error | string) => {
                              const m: string = typeof err === 'string' ? err : String(err?.message ?? err);
                              this.toastExport(`导出异常：${m}`);
                            });
                          })
                        }
                      }
                      .width('100%')
                      .alignItems(VerticalAlign.Center)
                    }
                  }
                  .width('100%')
                }

                Web({ src: $rawfile('chat_renderer.html'), controller: this.webCtrl })
                  /* 当前 SDK 的 @kit.ArkWeb 未导出 MixedMode；rawfile+HTTPS 依赖 fileAccess 等与 chat_renderer 内规范化 */
                  .javaScriptAccess(true)
                  .fileAccess(true)
                  .domStorageAccess(true)
                  .imageAccess(true)
                  .onlineImageAccess(true)
                  .metaViewport(true)
                  .onPageEnd(() => {
                    this.webReady = true;
                    this.startReplyHeightPolling();
                    if (this.pendingScript.length > 0) {
                      const script: string = this.pendingScript;
                      this.pendingScript = '';
                      this.webCtrl.runJavaScript(script);
                    }
                  })
                  .width('100%')
                  .height(this.hasConversation ? this.replyHeight : 1)
                  .borderRadius(18)
                  .backgroundColor('#00000000')
                  .opacity(this.hasConversation ? 1 : 0)
                  .constraintSize({ minHeight: 0, maxHeight: this.replyMaxHeight })
              }
              .width('100%')
              .justifyContent(FlexAlign.Start)
              .alignItems(HorizontalAlign.Start)
              .padding({
                left: 16,
                right: 16,
                top: this.hasConversation ? this.chatContentPadTop : 0,
                bottom: this.hasConversation ? this.chatContentPadBottom : 0
              })

              if (this.hasConversation) {
                Blank()
                  .width('100%')
                  .layoutWeight(1)
              }
            }
            .width('100%')
            .constraintSize({ minHeight: '100%' })
            .justifyContent(this.hasConversation ? FlexAlign.Start : FlexAlign.Center)
            .alignItems(HorizontalAlign.Start)
          }
          .width('100%')
          .height('100%')
          .scrollBar(BarState.Off)
          .scrollable(ScrollDirection.Vertical)
        }
        .layoutWeight(1)
        .width('100%')

        Row({ space: 10 }) {
          TextInput({ text: this.query, placeholder: this.defaultQuery, controller: this.queryController })
            .onChange((value: string) => {
              this.query = value;
            })
            .onFocus(() => {
              if (this.query.trim() === this.defaultQuery) {
                this.query = '';
              }
            })
            .placeholderColor('#9ca3af')
            .fontColor('#111827')
            .layoutWeight(1)
            .height(46)
            .backgroundColor('#e5e7eb')
            .borderRadius(18)
            .padding({ left: 12, right: 8, top: 8, bottom: 8 })

          Button('发送')
            .enabled(!(this.hasConversation && !this.isTerminalStatus()))
            .onClick(() => {
              this.sendToWeb();
            })
            .height(46)
            .width(64)
            .borderRadius(18)
            .backgroundColor('#2563eb')
        }
        .padding({ left: 16, right: 16, top: 6, bottom: 10 })
        .width('100%')
        .backgroundColor('#f8fafc')
      }
      .width('100%')
      .height('100%')
      .backgroundColor('#f2f4f7')

      if (this.showSettings) {
        Column() {
          Blank()
        }
        .width('100%')
        .height('100%')
        .backgroundColor('#66000000')
        .onClick(() => {
          this.closeSettings();
        })

        Column({ space: 10 }) {
          Row() {
            Text('模型设置')
              .fontSize(18)
              .fontWeight(FontWeight.Bold)
            Blank()
            Button('关闭')
              .fontSize(12)
              .backgroundColor('#f3f4f6')
              .fontColor('#374151')
              .borderRadius(16)
              .onClick(() => {
                this.closeSettings();
              })
          }
          .width('100%')

          TextInput({ placeholder: 'BACKEND_URL', text: this.draftBackendUrl })
            .onChange((value: string) => {
              this.draftBackendUrl = value;
            })
            .height(42)
            .width('100%')

          TextInput({ placeholder: 'BASE_URL（留空则走后端 env）', text: this.draftBaseUrl })
            .onChange((value: string) => {
              this.draftBaseUrl = value;
            })
            .height(42)
            .width('100%')

          TextInput({ placeholder: 'API_KEY（留空则走后端 env）', text: this.draftApiKey })
            .type(InputType.Password)
            .onChange((value: string) => {
              this.draftApiKey = value;
            })
            .height(42)
            .width('100%')

          TextInput({ placeholder: 'MODEL（留空则走后端 env）', text: this.draftModel })
            .onChange((value: string) => {
              this.draftModel = value;
            })
            .height(42)
            .width('100%')

          if (this.settingsError !== '') {
            Text(this.settingsError)
              .fontSize(12)
              .fontColor('#dc2626')
              .width('100%')
          }

          Row({ space: 10 }) {
            Button('取消')
              .layoutWeight(1)
              .backgroundColor('#f3f4f6')
              .fontColor('#374151')
              .borderRadius(18)
              .onClick(() => {
                this.closeSettings();
              })

            Button('保存')
              .layoutWeight(1)
              .backgroundColor('#2563eb')
              .fontColor('#ffffff')
              .borderRadius(18)
              .onClick(() => {
                this.saveSettings();
              })
          }
          .width('100%')
        }
        .width('88%')
        .padding(18)
        .backgroundColor('#ffffff')
        .borderRadius(24)
        .margin({ left: 16, right: 16, top: 120 })
      }
    }
    .width('100%')
    .height('100%')
  }
}