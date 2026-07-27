/**
 * Streaming HTML Sanitizer
 *
 * 流式 HTML 清理器，支持增量输入：
 * - 移除 <script> 标签及内容
 * - 移除 <style> 标签及内容
 * - 移除事件监听器属性 (onclick, onerror, etc.)
 *
 * 设计：类似 html-extractor 的增量模式
 * - 检测到危险内容开始，缓冲不输出
 * - 等到危险内容闭合，一次性清理输出
 */

export interface SanitizeResult {
  html: string;
  hasNewContent: boolean;
}

export class StreamingHtmlSanitizer {
  private buffer = "";
  // 是否正在累积危险内容
  private dangerousPending = false;

  /**
   * 增量清理
   */
  sanitize(token: string): SanitizeResult {
    if (!token) {
      return { html: "", hasNewContent: false };
    }

    this.buffer += token;
    const result = this.process();

    return result;
  }

  /**
   * 获取累积的 buffer
   */
  getBuffer(): string {
    return this.buffer;
  }

  /**
   * 重置状态
   */
  reset(): void {
    this.buffer = "";
    this.dangerousPending = false;
  }

  /**
   * 处理当前 buffer
   */
  private process(): SanitizeResult {
    // 如果正在累积危险内容，检查是否闭合
    if (this.dangerousPending) {
      const closed = this.checkDangerousClosed();
      if (!closed) {
        // 危险内容未闭合，buffer 保持，返回空
        return { html: "", hasNewContent: false };
      }
      // 危险内容已闭合，清理并输出
      return this.flushAndProcess();
    }

    // 检查是否有新的危险内容开始
    if (this.hasDangerousStart()) {
      // 检查是否整个内容都是危险标签
      const trimmed = this.buffer.trim();
      if (/^<(script|style)\b/i.test(trimmed) && !/<\/(script|style)>/i.test(trimmed)) {
        // 整个都是危险标签，缓冲
        this.dangerousPending = true;
        return { html: "", hasNewContent: false };
      }
      // 部分内容危险，提取安全部分
      return this.extractSafeAndKeepDangerous();
    }

    // 正常处理：移除已完成的危险内容 + 事件处理器，然后输出
    const cleaned = this.cleanContent(this.buffer);
    this.buffer = "";
    return { html: cleaned, hasNewContent: !!cleaned };
  }

  /**
   * 检查危险内容是否闭合
   */
  private checkDangerousClosed(): boolean {
    return /<\/(script|style)>/i.test(this.buffer);
  }

  /**
   * 检查是否有危险标签开始
   */
  private hasDangerousStart(): boolean {
    return /<(script|style)\b/i.test(this.buffer);
  }

  /**
   * 提取安全部分，保留危险部分在 buffer
   */
  private extractSafeAndKeepDangerous(): SanitizeResult {
    const lastGt = this.buffer.lastIndexOf(">");
    if (lastGt <= 0) {
      return { html: "", hasNewContent: false };
    }
    const safe = this.buffer.slice(0, lastGt + 1);
    const dangerous = this.buffer.slice(lastGt + 1);

    // 检查剩余是否危险
    this.buffer = dangerous;
    this.dangerousPending = /<(script|style)\b/i.test(dangerous.trim());

    const cleaned = this.cleanContent(safe);
    return { html: cleaned, hasNewContent: !!cleaned };
  }

  /**
   * 危险内容闭合后，清理并处理剩余
   */
  private flushAndProcess(): SanitizeResult {
    // 移除危险内容
    let content = this.buffer.replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, "");
    this.buffer = "";
    this.dangerousPending = false;

    // 清理事件处理器
    content = this.removeEventHandlers(content);

    if (!content) {
      return { html: "", hasNewContent: false };
    }

    // ��查剩余是否还有危险内容
    if (this.hasDangerousStart()) {
      this.buffer = content;
      return this.process();
    }

    return { html: content, hasNewContent: !!content.trim() };
  }

  /**
   * 清理内容
   */
  private cleanContent(content: string): string {
    // 移除 script 块
    let result = content.replace(/<script\b[\s\S]*?<\/script>/gi, "");
    // 移除 style 块
    result = result.replace(/<style\b[\s\S]*?<\/style>/gi, "");
    // 移除事件处理器
    result = this.removeEventHandlers(result);
    return result;
  }

  /**
   * 移除事件处理器属性
   */
  private removeEventHandlers(content: string): string {
    return content.replace(/\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  }
}

/**
 * 创建流式 HTML 清理器
 */
export function createStreamingHtmlSanitizer(): StreamingHtmlSanitizer {
  return new StreamingHtmlSanitizer();
}

/**
 * 批量清理（非流式）
 */
export function sanitizeHtml(fragment: string): string {
  // 移除 script 块
  let result = fragment.replace(/<script\b[\s\S]*?<\/script>/gi, "");
  // 移除 style 块
  result = result.replace(/<style\b[\s\S]*?<\/style>/gi, "");
  // 移除事件处理器
  result = result.replace(/\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  return result;
}