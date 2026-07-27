/**
 * 增量 DOM 渲染策略
 *
 * 使用 IncrementalHtmlParser 进行增量 DOM 构建
 * 注意：节流逻辑在 preview-renderer.ts 中统一处理
 */

import type { RenderContext } from "./interface";

import { createStreamingHtmlExtractor } from "../html-extractor";
import { createStreamingHtmlSanitizer } from "../html-sanitizer";

import { IncrementalHtmlParser } from "../incremental-html-parser";

// === Strategy 定义 ===

function createIncrementalStrategy() {
  const streamingHtmlExtractor = createStreamingHtmlExtractor();
  const streamingSanitizer = createStreamingHtmlSanitizer();
  let parser: IncrementalHtmlParser | null = null;

  return {
    name: "incremental" as const,

    onToken(token: string, context: RenderContext): boolean {
      const extracted = streamingHtmlExtractor.extract(token);

      if (!extracted.hasNewContent) return false;

      // 清理 HTML - 增量处理
      const sanitizedResult = streamingSanitizer.sanitize(extracted.html);
      if (!sanitizedResult.hasNewContent && !sanitizedResult.html) {
        // 没有新内容，可能是危险内容被缓冲
        return false;
      }

      // 初始化解析器（如果需要）
      if (!parser) {
        parser = new IncrementalHtmlParser({
          rootElement: context.rootElement as HTMLElement,
          debug: false
        });
      }

      // 使用增量解析器解析（由 preview-renderer 负责节流）
      parser.parse(sanitizedResult.html);
      return true;
    },

    onDone(context: RenderContext): void {
      parser = null;
    },

    reset(): void {
      parser = null;
      streamingHtmlExtractor.reset();
      streamingSanitizer.reset();
    },

    getLastRenderedHtml(context: RenderContext): string {
      return context.rootElement?.innerHTML ?? '';
    },
  };
}

export const incrementalStrategy = createIncrementalStrategy();