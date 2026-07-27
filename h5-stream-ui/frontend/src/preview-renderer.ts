/**
 * 预览渲染器 - 流式渲染的调度中心
 *
 * 负责节流：累积 token 后统一调用 strategy，避免过于频繁的 DOM 更新
 * 共用节流逻辑给 inner-html 和 incremental-dom 两种策略
 */

import { collectFragmentDiagnostics } from "./fragment-diagnostics";
import { incrementalStrategy } from "./render-strategies/incremental";
import { innerHtmlStrategy } from "./render-strategies/inner-html";
import type { RenderContext, RenderStrategy } from "./render-strategies/interface";

const MIN_RENDER_INTERVAL_MS = 280;

export interface RenderConfig {
  strategy: "inner-html" | "incremental-dom";
}

export interface RendererCallbacks {
  onRendered?: () => void;
}

export class PreviewRenderer {
  private diagnostics: string[] = [];
  private lastCommittedHtml: string = "";
  private strategy: RenderStrategy;

  // 节流相关：累积 token，节流周期到达后统一调用 strategy
  private pendingToken = "";
  private raf = 0;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private lastRenderAt = 0;

  constructor(
    private docGetter: () => Document | null,
    private config: RenderConfig,
    private callbacks: RendererCallbacks = {}
  ) {
    this.strategy = this.config.strategy == 'incremental-dom' ? incrementalStrategy : innerHtmlStrategy;
  }

  private getContext(): RenderContext {
    const doc = this.docGetter();
    return {
      rootElement: doc?.getElementById("root") ?? null,
      ownerDocument: doc,
    };
  }

  /** 节流周期到达，调用 strategy 处理累积的 token */
  private flushPendingToken(): void {
    if (!this.pendingToken) return;

    const ctx = this.getContext();
    const token = this.pendingToken;
    this.pendingToken = "";

    const rendered = this.strategy.onToken(token, ctx);
    if (rendered) {
      this.callbacks.onRendered?.();
    }
  }

  /** 调度更新（节流） */
  private scheduleUpdate(): void {
    if (this.raf || this.flushTimer !== null) return;

    const now = Date.now();
    const wait = Math.max(0, MIN_RENDER_INTERVAL_MS - (now - this.lastRenderAt));

    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.raf = requestAnimationFrame(() => {
        this.raf = 0;
        this.lastRenderAt = Date.now();
        this.flushPendingToken();
      });
    }, wait);
  }

  /** 接收 token，累积后节流调用 strategy */
  onToken(token: string): void {
    // 累积 token
    this.pendingToken += token;
    // 调度节流更新
    this.scheduleUpdate();
  }

  /** 流结束，透传给 strategy */
  onDone(): void {
    const ctx = this.getContext();

    // 先处理剩余的累积 token
    if (this.pendingToken) {
      this.flushPendingToken();
    }

    this.strategy.onDone(ctx);

    // 从 strategy 获取最终内容用于诊断
    this.lastCommittedHtml = this.strategy.getLastRenderedHtml?.(ctx) ?? "";
    this.diagnostics = collectFragmentDiagnostics(this.lastCommittedHtml);
  }

  /** 重置状态 */
  reset(): void {
    // 取消待定的节流
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    this.pendingToken = "";
    this.lastRenderAt = 0;

    this.diagnostics = [];
    this.lastCommittedHtml = "";
    this.strategy.reset();
  }

  /** 获取诊断信息（需在 onDone 后调用） */
  getDiagnostics(): string[] {
    return this.diagnostics;
  }
}