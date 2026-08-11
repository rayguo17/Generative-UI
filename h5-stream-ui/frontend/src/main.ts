import "./style.css";
import html2canvas from "html2canvas";
import { PreviewRenderer } from "./preview-renderer";
import { parseSseStream } from "./sse-stream";

const MODEL_STORAGE_KEY = "h5-stream-ui.model";
const BASE_URL_STORAGE_KEY = "h5-stream-ui.base-url";
const API_KEY_STORAGE_KEY = "h5-stream-ui.api-key";

const app = document.querySelector<HTMLDivElement>("#app")!;

app.innerHTML = `
<div class="playground">
  <header class="pg-top">
    <span class="pg-brand">H5 Playground</span>
    <div class="pg-top-right">
      <div class="cfg-menu">
        <button type="button" id="cfg-toggle" class="btn-text">模型配置</button>
        <div id="cfg-panel" class="cfg-panel hidden">
          <div class="cfg-title">模型配置</div>
          <label class="cfg-row">
            <span class="cfg-label">BASE_URL:</span>
            <input id="base-url" class="model-input" type="text" placeholder="https://..." />
          </label>
          <label class="cfg-row">
            <span class="cfg-label">API_KEY:</span>
            <input id="api-key" class="model-input" type="password" placeholder="sk-..." />
          </label>
          <label class="cfg-row">
            <span class="cfg-label">MODEL:</span>
            <input id="model" class="model-input" type="text" placeholder="glm-5.1 / gpt-4o-mini" />
          </label>
          <div id="cfg-message" class="cfg-message" aria-live="polite"></div>
          <div class="cfg-actions">
            <button type="button" id="cfg-cancel" class="btn btn-ghost">取消</button>
            <button type="button" id="cfg-save" class="btn">保存</button>
          </div>
        </div>
      </div>
      <button type="button" id="clear" class="btn-text">清空输出</button>
      <span id="status" class="pg-status"></span>
    </div>
  </header>
  <section class="pg-input-section">
    <h1 class="pg-title">想生成什么样的界面？</h1>
    <div class="composer card">
      <textarea id="query" class="composer-field" rows="6" placeholder="描述想生成的界面，需要的话在下面贴上 JSON 或数据正文…"></textarea>
      <div class="composer-bar">
        <div class="composer-bar-right">
          <button type="button" id="stop" class="btn btn-ghost" disabled>停止</button>
          <button type="button" id="go" class="btn btn-generate">
            <span id="go-idle">生成</span>
            <span id="go-loading" class="btn-loading hidden">
              <span class="btn-spinner" aria-hidden="true"></span>
              生成中<span class="loading-dots"><span>.</span><span>.</span><span>.</span></span>
            </span>
          </button>
        </div>
      </div>
    </div>
    <div class="chips" id="chips">
      <button type="button" class="chip" data-prompt="天气仪表盘：今日气温、降水、风速，再加 7 日预报表格。数据可自拟。">天气仪表盘</button>
      <button type="button" class="chip" data-prompt="三个定价卡片：基础版 / 专业版 / 企业版，带价格和简短功能列表。">定价卡片</button>
      <button type="button" class="chip" data-prompt="简易看板：三列「待办 / 进行中 / 已完成」，每列若干示例任务卡片。">看板</button>
      <button type="button" class="chip" data-prompt="登录表单：邮箱、密码、记住我、主按钮登录，风格简洁。">登录表单</button>
      <button type="button" class="chip" data-prompt="数据表：姓名、部门、工号、状态四列，带搜索框和分页。分页按钮请绑定 data-interactions 的 setPage 事件（group 可用 emp，页码 1/2/3），并用 data-page-group/data-page 与 data-page-btn-group/data-page-btn 标注页面与页码按钮。">数据表格</button>
    </div>
  </section>
  <section class="pg-output">
    <div class="output-col output-raw" id="raw-col">
      <div class="output-raw-head">
        <span>RAW OUTPUT</span>
        <button type="button" id="copy-raw" class="icon-btn" title="复制 RAW 输出">
          <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="3.5" width="9.5" height="12.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="3.5" y="7" width="9.5" height="9.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>
        </button>
      </div>
      <div class="output-body" id="raw-body">
        <pre id="raw" class="stream-pre"></pre>
        <div id="raw-waiting" class="raw-waiting hidden" aria-live="polite">
          <span class="btn-spinner" aria-hidden="true"></span>
          <span id="raw-waiting-text">正在等待首个 token…</span>
        </div>
        <div id="raw-diagnostics" class="raw-diagnostics hidden" aria-live="polite"></div>
        <div class="pane-placeholder raw-placeholder-el" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.2"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" /></svg>
          <span>模型原始输出会出现在这里</span>
        </div>
      </div>
    </div>
    <div class="output-col output-preview">
      <div class="output-head">
        <span>PREVIEW</span>
        <div class="output-head-right">
          <button type="button" id="screenshot-preview" class="icon-btn" title="截图（当前视口模式下的完整预览，含圆角）">
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" d="M5.5 5.5h2.2M12.3 5.5h2.2M5.5 14.5h2.2M12.3 14.5h2.2M5.5 5.5V7.7M14.5 5.5V7.7M5.5 14.5v-2.2M14.5 14.5v-2.2"/>
              <rect x="5" y="7" width="10" height="7" rx="2" fill="none" stroke="currentColor" stroke-width="1.15"/>
              <circle cx="10" cy="10.5" r="1.35" fill="none" stroke="currentColor" stroke-width="1.05"/>
            </svg>
          </button>
          <button type="button" id="download-html" class="icon-btn" title="下载完整 HTML">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M10 3.5v8.8"/><path fill="none" stroke="currentColor" stroke-width="1.3" d="M6.8 9.6L10 12.8l3.2-3.2"/><rect x="3.5" y="14" width="13" height="2.5" rx="1.1" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>
          </button>
          <div class="viewport-toggles">
          <button type="button" class="vtoggle active" data-preview="full" title="预览区全宽（不限定机型宽度）">
            <svg class="vtoggle-icon vtoggle-icon--wide" viewBox="0 0 20 20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M2.5 5h15v10h-15z"/><path stroke="currentColor" stroke-width="1.3" d="M10 5v10"/></svg>
            <span class="vtoggle-label">全宽</span>
          </button>
          <button type="button" class="vtoggle" data-preview="phone" title="华为直屏机逻辑宽约420px（约 1260px 短边 @3x，如 Mate / Pura 外屏）">
            <svg class="vtoggle-icon" viewBox="0 0 20 20" aria-hidden="true"><rect x="6" y="2.5" width="8" height="15" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="8.2" y="15.4" width="3.6" height="1.1" rx="0.55" fill="currentColor" opacity="0.45"/></svg>
            <span class="vtoggle-label">直屏</span>
          </button>
          <button type="button" class="vtoggle" data-preview="fold" title="华为双折叠内屏预览宽约 744px（2224px 内屏短边按常见 @3x 折算）">
            <svg class="vtoggle-icon" viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3.5" width="6.5" height="13" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="11" y="3.5" width="6.5" height="13" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2"/><path stroke="currentColor" stroke-width="1" d="M10 4.5v11" stroke-dasharray="1.5 1.5"/></svg>
            <span class="vtoggle-label">双折叠</span>
          </button>
          <button type="button" class="vtoggle" data-preview="tablet" title="华为三折叠·平板预览宽约 1192px（约 3184/@2.67 量级）">
            <svg class="vtoggle-icon" viewBox="0 0 20 20" aria-hidden="true"><rect x="1.5" y="4" width="4.8" height="12" rx="0.9" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="7.6" y="4" width="4.8" height="12" rx="0.9" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="13.7" y="4" width="4.8" height="12" rx="0.9" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>
            <span class="vtoggle-label">三折叠·平板</span>
          </button>
          </div>
        </div>
      </div>
      <div class="preview-shell" id="preview-shell">
        <div class="preview-frame-wrap vp-full" id="preview-wrap">
          <iframe id="frame" class="frame" title="preview" sandbox="allow-scripts allow-same-origin"></iframe>
        </div>
        <div class="pane-placeholder preview-placeholder" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.2"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3A1.5 1.5 0 001.5 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" /></svg>
          <span>渲染结果会出现在这里</span>
        </div>
      </div>
    </div>
  </section>
</div>
`;

const elQuery = document.querySelector<HTMLTextAreaElement>("#query")!;
const elModel = document.querySelector<HTMLInputElement>("#model")!;
const elBaseUrl = document.querySelector<HTMLInputElement>("#base-url")!;
const elApiKey = document.querySelector<HTMLInputElement>("#api-key")!;
const elGo = document.querySelector<HTMLButtonElement>("#go")!;
const elStop = document.querySelector<HTMLButtonElement>("#stop")!;
const elGoIdle = document.querySelector<HTMLSpanElement>("#go-idle")!;
const elGoLoading = document.querySelector<HTMLSpanElement>("#go-loading")!;
const elRaw = document.querySelector<HTMLPreElement>("#raw")!;
const elRawWaiting = document.querySelector<HTMLDivElement>("#raw-waiting")!;
const elRawWaitingText = document.querySelector<HTMLSpanElement>("#raw-waiting-text")!;
const elRawDiagnostics = document.querySelector<HTMLDivElement>("#raw-diagnostics")!;
const elStatus = document.querySelector<HTMLSpanElement>("#status")!;
const elFrame = document.querySelector<HTMLIFrameElement>("#frame")!;
const elCopyRaw = document.querySelector<HTMLButtonElement>("#copy-raw")!;
const elScreenshotPreview = document.querySelector<HTMLButtonElement>("#screenshot-preview")!;
const elDownloadHtml = document.querySelector<HTMLButtonElement>("#download-html")!;
const elCfgToggle = document.querySelector<HTMLButtonElement>("#cfg-toggle")!;
const elCfgPanel = document.querySelector<HTMLDivElement>("#cfg-panel")!;
const elCfgSave = document.querySelector<HTMLButtonElement>("#cfg-save")!;
const elCfgCancel = document.querySelector<HTMLButtonElement>("#cfg-cancel")!;
const elCfgMessage = document.querySelector<HTMLDivElement>("#cfg-message")!;
const elRawCol = document.querySelector<HTMLDivElement>("#raw-col")!;
const elPreviewShell = document.querySelector<HTMLDivElement>("#preview-shell")!;
const elPreviewWrap = document.querySelector<HTMLDivElement>("#preview-wrap")!;
const elChips = document.querySelector<HTMLDivElement>("#chips")!;
const elClear = document.querySelector<HTMLButtonElement>("#clear")!;

// 创建渲染器
// 注意：elFrame.contentDocument 在初始化时可能是 null，需要在渲染时动态获取
const renderer = new PreviewRenderer(
  () => elFrame.contentDocument,
  { strategy: "inner-html" },
  {
    onRendered: () => {
      elPreviewShell.classList.add("has-preview");
      applyPreviewModeToIframeDocument();
      applyPersistedPageStates();
      schedulePreviewHeightSync();
    },
  }
);

elModel.value = localStorage.getItem(MODEL_STORAGE_KEY) ?? "";
elBaseUrl.value = localStorage.getItem(BASE_URL_STORAGE_KEY) ?? "";
elApiKey.value = localStorage.getItem(API_KEY_STORAGE_KEY) ?? "";
elModel.addEventListener("input", () => localStorage.setItem(MODEL_STORAGE_KEY, elModel.value.trim()));
elBaseUrl.addEventListener("input", () => localStorage.setItem(BASE_URL_STORAGE_KEY, elBaseUrl.value.trim()));
elApiKey.addEventListener("input", () => localStorage.setItem(API_KEY_STORAGE_KEY, elApiKey.value));

function closeConfigPanel() {
  elCfgPanel.classList.add("hidden");
  elCfgMessage.textContent = "";
  elCfgMessage.classList.remove("error", "success");
}

function openConfigPanel() {
  elCfgPanel.classList.remove("hidden");
}

function setConfigMessage(message: string, kind: "error" | "success") {
  elCfgMessage.textContent = message;
  elCfgMessage.classList.remove("error", "success");
  elCfgMessage.classList.add(kind);
}

elCfgToggle.addEventListener("click", () => {
  const hidden = elCfgPanel.classList.contains("hidden");
  if (hidden) openConfigPanel();
  else closeConfigPanel();
});

elCfgSave.addEventListener("click", () => {
  const model = elModel.value.trim();
  const baseUrl = elBaseUrl.value.trim();
  const apiKey = elApiKey.value.trim();
  if (model) localStorage.setItem(MODEL_STORAGE_KEY, model);
  else localStorage.removeItem(MODEL_STORAGE_KEY);
  if (baseUrl) localStorage.setItem(BASE_URL_STORAGE_KEY, baseUrl);
  else localStorage.removeItem(BASE_URL_STORAGE_KEY);
  if (apiKey) localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
  else localStorage.removeItem(API_KEY_STORAGE_KEY);
  closeConfigPanel();
});

elCfgCancel.addEventListener("click", () => {
  elModel.value = localStorage.getItem(MODEL_STORAGE_KEY) ?? "";
  elBaseUrl.value = localStorage.getItem(BASE_URL_STORAGE_KEY) ?? "";
  elApiKey.value = localStorage.getItem(API_KEY_STORAGE_KEY) ?? "";
  closeConfigPanel();
});

document.addEventListener("click", (ev) => {
  const target = ev.target as Node | null;
  if (!target) return;
  if (elCfgPanel.classList.contains("hidden")) return;
  if (elCfgPanel.contains(target) || elCfgToggle.contains(target)) return;
  closeConfigPanel();
});

let abort: AbortController | null = null;
let raf = 0;
let previewFlushTimer: number | null = null;
let pendingFragment = "";
let lastCommittedFragment = "";
let lastRenderAt = 0;
let previewShellReady = false;
let generating = false;
let streamAcc = "";
let firstTokenArrived = false;
let requestStartedAt = 0;
let ttftMs: number | null = null;
let waitingTicker: number | null = null;
let currentPreviewMode: "full" | "phone" | "fold" | "tablet" = "full";
const pageStateByGroup = new Map<string, number>();
let previewHeightSyncTimer: number | null = null;
const PREVIEW_MIN_HEIGHT = 180;
const PREVIEW_MAX_HEIGHT_BY_MODE: Record<"full" | "phone" | "fold" | "tablet", number> = {
  full: 1200,
  phone: 860,
  fold: 920,
  tablet: 980,
};

/** Matches `.frame` / device lane border-radius in style.css */
const PREVIEW_FRAME_RADIUS_CSS_PX = 20;

function getIframeFullDocumentSize(doc: Document): { width: number; height: number } {
  const de = doc.documentElement;
  const body = doc.body;
  const width = Math.max(de.scrollWidth, body.scrollWidth, de.clientWidth, body.clientWidth);
  const height = Math.max(de.scrollHeight, body.scrollHeight, de.clientHeight, body.clientHeight);
  return { width: Math.ceil(width), height: Math.ceil(height) };
}

function applyRoundedCornersToCanvas(src: HTMLCanvasElement, radiusCssPx: number, scale: number): HTMLCanvasElement {
  const maxR = Math.min(src.width, src.height) / 2;
  const r = Math.min(radiusCssPx * scale, maxR);
  const out = document.createElement("canvas");
  out.width = src.width;
  out.height = src.height;
  const ctx = out.getContext("2d");
  if (!ctx) return src;
  ctx.beginPath();
  ctx.roundRect(0, 0, out.width, out.height, r);
  ctx.clip();
  ctx.drawImage(src, 0, 0);
  return out;
}

/** Host shell: head + Tailwind + #root. Model output is only the inner HTML fragment. */
const SHELL_PREFIX = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<script src="https://cdn.tailwindcss.com"></script>
<style>
html,body{margin:0;padding:0;}
#root{display:block;}
/* Host-side adaptive override for fold/tablet lanes:
   if model hard-codes small max-width (e.g. max-w-[420px]),
   expand first root container to lane width in larger modes. */
body[data-preview-mode="fold"] #root > :first-child,
body[data-preview-mode="tablet"] #root > :first-child{
  width: 100% !important;
  max-width: 100% !important;
}
</style>
<script src="https://cdn.jsdelivr.net/npm/genui-widgets@latest/dist/genui-widgets.umd.min.js"></script>
</head>
<body>
<div id="root">`;

const SHELL_SUFFIX = `</div>
<script>
(() => {
  function parseInteractions(el) {
    if (!el) return null;
    const raw = el.getAttribute("data-interactions");
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return null; }
  }
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const node = target.closest("[data-interactions]");
    if (!node) return;
    const interactions = parseInteractions(node);
    const onClick = interactions && Array.isArray(interactions.onClick) ? interactions.onClick : [];
    if (!onClick.length) return;
    event.preventDefault();
    window.parent.postMessage({
      source: "h5-stream-ui-bridge",
      type: "interaction",
      event: "onClick",
      actions: onClick
    }, "*");
  }, true);
})();
</script>
</body>
</html>`;

const ALLOWED_PROTOCOLS = new Set(["https:"]);
const IS_DEV = import.meta.env.DEV;
const ALLOWED_HOSTS = [
  "leisu.com",
  "www.leisu.com",
  "miguvideo.com",
  "www.miguvideo.com",
];

type InteractionItem = {
  type?: string;
  params?: Record<string, unknown>;
};

type SetPageParams = {
  group: string;
  page?: number;
  delta?: number;
};

function isAllowedHost(hostname: string): boolean {
  const lower = hostname.toLowerCase();
  return ALLOWED_HOSTS.some((h) => lower === h || lower.endsWith(`.${h}`));
}

function safeOpenUrl(rawUrl: unknown): boolean {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) return false;
  try {
    const url = new URL(rawUrl);
    if (!ALLOWED_PROTOCOLS.has(url.protocol)) return false;
    if (!IS_DEV && !isAllowedHost(url.hostname)) return false;
    window.open(url.toString(), "_blank", "noopener,noreferrer");
    return true;
  } catch {
    return false;
  }
}

function parseSetPageParams(raw: Record<string, unknown> | undefined): SetPageParams | null {
  if (!raw) return null;
  const group = typeof raw.group === "string" ? raw.group.trim() : "";
  if (!group) return null;
  const parseIntLike = (v: unknown): number | undefined => {
    if (typeof v === "number" && Number.isFinite(v)) return Math.trunc(v);
    if (typeof v === "string" && v.trim()) {
      const n = Number.parseInt(v.trim(), 10);
      if (Number.isFinite(n)) return n;
    }
    return undefined;
  };
  const page = parseIntLike(raw.page);
  const delta = parseIntLike(raw.delta);
  if (page === undefined && delta === undefined) return null;
  return { group, page, delta };
}

function applySetPage(params: SetPageParams): boolean {
  const doc = elFrame.contentDocument;
  if (!doc) return false;
  const inGroup = (node: Element, ownKey: string): boolean => {
    const own = (node as HTMLElement).dataset[ownKey];
    if (own === params.group) return true;
    const host = node.closest<HTMLElement>(`[data-page-group="${params.group}"],[data-page-btn-group="${params.group}"],[data-page-indicator-group="${params.group}"]`);
    return Boolean(host);
  };

  const pageNodes = Array.from(doc.querySelectorAll<HTMLElement>("[data-page]")).filter((n) => inGroup(n, "pageGroup"));
  if (!pageNodes.length) return false;

  const pages = pageNodes
    .map((n) => Number.parseInt(n.dataset.page || "", 10))
    .filter((n) => Number.isFinite(n));
  if (!pages.length) return false;

  const minPage = Math.min(...pages);
  const maxPage = Math.max(...pages);
  const currentNode =
    pageNodes.find((n) => !n.hasAttribute("hidden")) ??
    pageNodes.find((n) => Number.parseInt(n.dataset.page || "", 10) === minPage);
  const current = Number.parseInt(currentNode?.dataset.page || String(minPage), 10) || minPage;

  let target = params.page ?? (current + (params.delta ?? 0));
  if (!Number.isFinite(target)) return false;
  target = Math.max(minPage, Math.min(maxPage, target));

  let changed = false;
  for (const node of pageNodes) {
    const p = Number.parseInt(node.dataset.page || "", 10);
    const active = p === target;
    if (active) node.removeAttribute("hidden");
    else node.setAttribute("hidden", "");
    changed = changed || active;
  }

  const btns = Array.from(doc.querySelectorAll<HTMLElement>("[data-page-btn]")).filter((n) => inGroup(n, "pageBtnGroup"));
  for (const btn of btns) {
    const p = Number.parseInt(btn.dataset.pageBtn || "", 10);
    const active = p === target;
    btn.setAttribute("aria-current", active ? "page" : "false");
    btn.classList.toggle("ring-2", active);
    btn.classList.toggle("ring-blue-500", active);
    btn.classList.toggle("text-white", active);
    btn.classList.toggle("bg-[#0A59F7]", active);
    if (!active) {
      btn.classList.remove("ring-2", "ring-blue-500", "text-white", "bg-[#0A59F7]");
    }
  }

  const indicator =
    doc.querySelector<HTMLElement>(`[data-page-indicator-group="${params.group}"]`) ??
    doc.querySelector<HTMLElement>("[data-page-indicator]");
  if (indicator) {
    const total = indicator.dataset.pageTotal || String(maxPage);
    indicator.textContent = `${target} / ${total} 页`;
  }

  if (changed) {
    pageStateByGroup.set(params.group, target);
  }
  return changed;
}

function applyPersistedPageStates() {
  for (const [group, page] of pageStateByGroup.entries()) {
    applySetPage({ group, page });
  }
}

function syncFrameHeightToContent() {
  const doc = elFrame.contentDocument;
  if (!doc?.body || !doc.documentElement) return;
  const root = doc.getElementById("root");
  const first = root?.firstElementChild as HTMLElement | null;
  const firstHeight =
    first
      ? Math.max(
          first.scrollHeight,
          first.offsetHeight,
          Math.ceil(first.getBoundingClientRect().height),
        )
      : 0;
  const rootHeight = Math.max(root?.scrollHeight ?? 0, root?.offsetHeight ?? 0);
  const contentHeight = Math.max(firstHeight, rootHeight);
  const available = Math.max(PREVIEW_MIN_HEIGHT, elPreviewWrap.clientHeight - 24);
  const maxByMode = PREVIEW_MAX_HEIGHT_BY_MODE[currentPreviewMode] ?? PREVIEW_MAX_HEIGHT_BY_MODE.full;
  const maxHeight = Math.min(maxByMode, available);
  const needsScroll = contentHeight > maxHeight;
  const targetHeight = needsScroll ? maxHeight : contentHeight;
  const next = Math.max(PREVIEW_MIN_HEIGHT, Math.min(targetHeight, maxHeight));
  doc.documentElement.style.overflowY = needsScroll ? "auto" : "hidden";
  doc.body.style.overflowY = needsScroll ? "auto" : "hidden";
  elFrame.style.height = `${next}px`;
  if (generating) {
    requestAnimationFrame(() => {
      const d = elFrame.contentDocument;
      if (!d || !generating) return;
      const se = d.scrollingElement ?? d.documentElement;
      se.scrollTop = se.scrollHeight;
    });
  }
}

function schedulePreviewHeightSync() {
  if (previewHeightSyncTimer !== null) {
    window.clearTimeout(previewHeightSyncTimer);
  }
  previewHeightSyncTimer = window.setTimeout(() => {
    previewHeightSyncTimer = null;
    syncFrameHeightToContent();
  }, 16);
}

const VIEWPORT_CLASSES = ["vp-full", "vp-phone", "vp-fold", "vp-tablet"] as const;

function buildCurrentHtmlDocument(): string | null {
  const doc = elFrame.contentDocument;
  const root = doc?.getElementById("root");
  const frag = root?.innerHTML?.trim() ?? "";
  if (!frag) return null;
  return SHELL_PREFIX + frag.replace(/<\/script/gi, "<\\/script") + SHELL_SUFFIX;
}

function resetPreviewShell() {
  renderer.reset();
  pageStateByGroup.clear();
  previewShellReady = false;
  elFrame.srcdoc = SHELL_PREFIX + SHELL_SUFFIX;
}

function syncRawEmpty() {
  const empty = !generating && streamAcc.length === 0;
  elRawCol.classList.toggle("empty", empty);
}

function syncRawWaiting() {
  const waiting = generating && !firstTokenArrived;
  elRawWaiting.classList.toggle("hidden", !waiting);
}

function setGeneratingButtonState(isGenerating: boolean) {
  elGoIdle.classList.toggle("hidden", isGenerating);
  elGoLoading.classList.toggle("hidden", !isGenerating);
}

function showDiagnostics(messages: string[]) {
  if (!messages.length) {
    elRawDiagnostics.classList.add("hidden");
    elRawDiagnostics.textContent = "";
    return;
  }
  const hasOpenUrlBlocked = messages.includes("openUrl 不是 https（宿主会拦截）");
  const prefix = hasOpenUrlBlocked
    ? "可渲染，但部分点击事件在宿主不可执行（需 https 链接）："
    : "检测到问题：";
  elRawDiagnostics.classList.remove("hidden");
  elRawDiagnostics.textContent = `${prefix}${messages.join("；")}`;
}

function applyPreviewModeToIframeDocument() {
  const doc = elFrame.contentDocument;
  if (!doc?.body) return;
  doc.body.setAttribute("data-preview-mode", currentPreviewMode);
}

function fmtSecs(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function stopWaitingTicker() {
  if (waitingTicker !== null) {
    window.clearInterval(waitingTicker);
    waitingTicker = null;
  }
}

function startWaitingTicker() {
  stopWaitingTicker();
  waitingTicker = window.setInterval(() => {
    if (!generating || firstTokenArrived || requestStartedAt <= 0) return;
    const elapsed = Date.now() - requestStartedAt;
    elRawWaitingText.textContent = `正在等待首个 token… ${fmtSecs(elapsed)}`;
    elStatus.textContent = `生成中（等待首 token ${fmtSecs(elapsed)}）`;
  }, 100);
}

document.querySelectorAll<HTMLButtonElement>(".vtoggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll<HTMLButtonElement>(".vtoggle").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const mode = btn.dataset.preview;
    elPreviewWrap.classList.remove(...VIEWPORT_CLASSES);
    if (mode === "phone" || mode === "fold" || mode === "tablet") {
      currentPreviewMode = mode;
      elPreviewWrap.classList.add(`vp-${mode}`);
    } else {
      currentPreviewMode = "full";
      elPreviewWrap.classList.add("vp-full");
    }
    applyPreviewModeToIframeDocument();
    schedulePreviewHeightSync();
  });
});

elFrame.addEventListener("load", () => {
  previewShellReady = true;
  applyPreviewModeToIframeDocument();
  applyPersistedPageStates();
  schedulePreviewHeightSync();
});

elChips.addEventListener("click", (e) => {
  const t = (e.target as HTMLElement).closest<HTMLButtonElement>(".chip");
  if (!t?.dataset.prompt) return;
  elQuery.value = t.dataset.prompt;
  elQuery.focus();
});

function buildRequestBody(): Record<string, unknown> {
  const body: Record<string, unknown> = { query: elQuery.value };
  const m = elModel.value.trim();
  const baseUrl = elBaseUrl.value.trim();
  const apiKey = elApiKey.value.trim();
  if (m) body.model = m;
  if (baseUrl) body.base_url = baseUrl;
  if (apiKey) body.api_key = apiKey;
  return body;
}

elGo.addEventListener("click", async () => {
  if (!elQuery.value.trim()) {
    elStatus.textContent = "请先填写输入";
    return;
  }
  if (abort) abort.abort();
  abort = new AbortController();
  generating = true;
  firstTokenArrived = false;
  ttftMs = null;
  requestStartedAt = Date.now();
  elRawWaitingText.textContent = "正在等待首个 token… 0.0s";
  startWaitingTicker();
  syncRawEmpty();
  syncRawWaiting();
  showDiagnostics([]);
  elGo.disabled = true;
  setGeneratingButtonState(true);
  elStop.disabled = false;
  elStatus.textContent = "生成中…";
  streamAcc = "";
  elRaw.textContent = "";
  elPreviewShell.classList.remove("has-preview");
  resetPreviewShell();
  let acc = "";
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequestBody()),
      signal: abort.signal,
    });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(errText || res.statusText);
    }
    await parseSseStream(res.body, {
      onToken: (token) => {
        if (!firstTokenArrived) {
          firstTokenArrived = true;
          ttftMs = Date.now() - requestStartedAt;
          stopWaitingTicker();
          elStatus.textContent = `首 token ${fmtSecs(ttftMs)}，继续生成中…`;
          syncRawWaiting();
        }
        acc += token;
        streamAcc = acc;
        elRaw.textContent = acc;
        syncRawEmpty();
        elRaw.scrollTop = elRaw.scrollHeight;
        // 使用渲染器处理 token
        renderer.onToken(token);
      },
      onDone: () => {
        renderer.onDone();
        const diagnostics = renderer.getDiagnostics();
        showDiagnostics(diagnostics);
        elStatus.textContent = ttftMs === null ? "完成" : `完成（TTFT ${fmtSecs(ttftMs)}）`;
        if (diagnostics.length) {
          elStatus.textContent = `完成（TTFT ${fmtSecs(ttftMs ?? 0)}，检测到 ${diagnostics.length} 项问题）`;
        }
      },
      onError: (err) => {
        throw err;
      },
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      elStatus.textContent = "已停止";
    } else {
      elStatus.textContent = "出错";
      elRaw.textContent += `\n\n[error] ${(e as Error).message}`;
    }
  } finally {
    stopWaitingTicker();
    generating = false;
    firstTokenArrived = false;
    requestStartedAt = 0;
    streamAcc = acc;
    syncRawEmpty();
    syncRawWaiting();
    setGeneratingButtonState(false);
    elGo.disabled = false;
    elStop.disabled = true;
    abort = null;
  }
});

elStop.addEventListener("click", () => abort?.abort());

elClear.addEventListener("click", () => {
  stopWaitingTicker();
  streamAcc = "";
  elRaw.textContent = "";
  showDiagnostics([]);
  resetPreviewShell();
  elPreviewShell.classList.remove("has-preview");
  generating = false;
  firstTokenArrived = false;
  requestStartedAt = 0;
  ttftMs = null;
  syncRawEmpty();
  syncRawWaiting();
  setGeneratingButtonState(false);
  elStatus.textContent = "已清空";
});

elCopyRaw.addEventListener("click", async () => {
  const text = elRaw.textContent ?? "";
  if (!text.trim()) {
    elStatus.textContent = "暂无可复制内容";
    return;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    elStatus.textContent = "RAW 输出已复制";
  } catch {
    elStatus.textContent = "复制失败（请检查浏览器权限）";
  }
});

elScreenshotPreview.addEventListener("click", async () => {
  if (!elPreviewShell.classList.contains("has-preview")) {
    elStatus.textContent = "暂无可截图的预览";
    return;
  }
  const doc = elFrame.contentDocument;
  if (!doc?.body || !previewShellReady) {
    elStatus.textContent = "预览未就绪";
    return;
  }
  const { width, height } = getIframeFullDocumentSize(doc);
  if (width < 2 || height < 2) {
    elStatus.textContent = "预览尺寸无效，无法截图";
    return;
  }
  elScreenshotPreview.disabled = true;
  elStatus.textContent = "正在生成截图…";
  const scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  try {
    const rawCanvas = await html2canvas(doc.documentElement, {
      scale,
      useCORS: true,
      allowTaint: false,
      logging: false,
      backgroundColor: "#ffffff",
      width,
      height,
      windowWidth: width,
      windowHeight: height,
      scrollX: 0,
      scrollY: 0,
      x: 0,
      y: 0,
      onclone: (_doc, el) => {
        const root = el as HTMLElement;
        root.style.overflow = "visible";
        root.style.height = `${height}px`;
        root.style.minHeight = `${height}px`;
        const b = _doc.body;
        b.style.overflow = "visible";
        b.style.height = `${height}px`;
        b.style.minHeight = `${height}px`;
      },
    });
    const rounded = applyRoundedCornersToCanvas(rawCanvas, PREVIEW_FRAME_RADIUS_CSS_PX, scale);
    await new Promise<void>((resolve, reject) => {
      rounded.toBlob(
        (blob) => {
          if (!blob) {
            reject(new Error("导出 PNG 失败"));
            return;
          }
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `preview-${currentPreviewMode}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
          resolve();
        },
        "image/png",
        0.95,
      );
    });
    elStatus.textContent = "已下载截图";
  } catch (e) {
    elStatus.textContent = `截图失败：${(e as Error).message}`;
  } finally {
    elScreenshotPreview.disabled = false;
  }
});

elDownloadHtml.addEventListener("click", () => {
  const html = buildCurrentHtmlDocument();
  if (!html) {
    elStatus.textContent = "暂无可下载的预览内容";
    return;
  }
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `generated-ui-${new Date().toISOString().replace(/[:.]/g, "-")}.html`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  elStatus.textContent = "已下载 HTML";
});

fetch("/health")
  .then(() => {
    /* no-op: do not show health text in top-right */
  })
  .catch(() => {
    /* no-op: connectivity hint is surfaced during generate flow */
  });

resetPreviewShell();

window.addEventListener("message", (ev: MessageEvent) => {
  const data = ev.data as {
    source?: string;
    type?: string;
    event?: string;
    actions?: InteractionItem[];
  };
  if (data?.source !== "h5-stream-ui-bridge") return;
  if (data?.type !== "interaction") return;
  if (data?.event !== "onClick") return;
  const actions = Array.isArray(data.actions) ? data.actions : [];
  for (const item of actions) {
    if (item?.type === "openUrl") {
      const ok = safeOpenUrl(item.params?.url);
      elStatus.textContent = ok ? "已通过宿主打开链接" : "链接被安全策略拦截";
      if (ok) break;
      continue;
    }
    if (item?.type === "setPage") {
      const params = parseSetPageParams(item.params);
      const ok = params ? applySetPage(params) : false;
      elStatus.textContent = ok ? "已切换分页" : "分页事件未生效（请检查 data-page-* 标注）";
      if (ok) schedulePreviewHeightSync();
      if (ok) break;
    }
  }
});

window.addEventListener("resize", () => {
  schedulePreviewHeightSync();
});

syncRawEmpty();
syncRawWaiting();


