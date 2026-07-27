/**
 * innerHTML 渲染策略 - 全量替换渲染
 *
 * 内部处理：累积 + 提取 + 清理 + 裁剪 + 渲染
 */

import type { RenderContext } from "./interface";

// === 接口定义 ===

interface ExtractionResult {
  html: string;
  isEmpty: boolean;
}

// === 内部函数：从 html-extractor.ts 移入 ===

function stripMarkdownFence(s: string): string {
  let t = s.trim();
  const fenceStart = t.indexOf("```");
  if (fenceStart >= 0) {
    t = t.slice(fenceStart);
    t = t.replace(/^```[a-zA-Z]*\s*/, "");
    t = t.replace(/\s*```$/, "");
  }
  return t;
}

function stripToFirstJsonObject(s: string): string {
  const i = s.indexOf("{");
  return i >= 0 ? s.slice(i) : s;
}

function findHtmlJsonStringStart(s: string): number | null {
  const m = /\{\s*"html"\s*:\s*"/.exec(s);
  return m ? m.index + m[0].length : null;
}

function decodeJsonStringPrefix(
  s: string,
  start: number,
): { text: string; closed: boolean } {
  let out = "";
  let i = start;
  while (i < s.length) {
    const c = s[i]!;
    if (c === '"') {
      return { text: out, closed: true };
    }
    if (c === "\\") {
      if (i + 1 >= s.length) {
        return { text: out, closed: false };
      }
      const n = s[i + 1]!;
      if (n === '"' || n === "\\" || n === "/") {
        out += n;
        i += 2;
        continue;
      }
      if (n === "b") {
        out += "\b";
        i += 2;
        continue;
      }
      if (n === "f") {
        out += "\f";
        i += 2;
        continue;
      }
      if (n === "n") {
        out += "\n";
        i += 2;
        continue;
      }
      if (n === "r") {
        out += "\r";
        i += 2;
        continue;
      }
      if (n === "t") {
        out += "\t";
        i += 2;
        continue;
      }
      if (n === "u") {
        if (i + 6 > s.length) {
          return { text: out, closed: false };
        }
        const hex = s.slice(i + 2, i + 6);
        if (/^[0-9a-fA-F]{4}$/.test(hex)) {
          out += String.fromCharCode(parseInt(hex, 16));
        }
        i += 6;
        continue;
      }
      out += n;
      i += 2;
      continue;
    }
    out += c;
    i++;
  }
  return { text: out, closed: false };
}

/** 从原始输出提取可渲染 HTML */
function extractHtml(buffer: string): ExtractionResult {
  const t = stripMarkdownFence(buffer);
  const trimmed = t.trimStart();
  if (trimmed.startsWith("{")) {
    const inner = stripToFirstJsonObject(trimmed);
    const start = findHtmlJsonStringStart(inner);
    if (start !== null) {
      const { text } = decodeJsonStringPrefix(inner, start);
      if (text.trim()) return { html: text, isEmpty: false };
    }
  }
  const lt = t.indexOf("<");
  if (lt < 0) return { html: "", isEmpty: true };
  return { html: t.slice(lt), isEmpty: false };
}

// === 内部函数：从 html-sanitizer.ts 移入 ===

/** 移除不安全的 HTML 内容 */
function sanitizeHtml(fragment: string): string {
  let t = fragment.replace(/<script\b[\s\S]*?<\/script>/gi, "");
  const loScript = t.toLowerCase().lastIndexOf("<script");
  if (loScript >= 0) t = t.slice(0, loScript);
  t = t.replace(/<style\b[\s\S]*?<\/style>/gi, "");
  const loStyle = t.toLowerCase().lastIndexOf("<style");
  if (loStyle >= 0) t = t.slice(0, loStyle);
  t = t.replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  return t;
}

// === 内部函数：从原 inner-html.ts 移入 ===

/** 流式裁剪 - 去除末尾不完整的 HTML 标签 */
function trimForStreaming(fragment: string): string {
  const t = fragment.trimEnd();
  const lastLt = t.lastIndexOf("<");
  const lastGt = t.lastIndexOf(">");

  if (lastLt === -1) return t;
  if (lastGt === -1 || lastLt > lastGt) {
    return t.slice(0, lastLt);
  }
  return t;
}

// === Strategy 定义 ===

function createInnerHtmlStrategy() {
  let acc = "";
  let lastCommitted = "";

  return {
    name: "inner-html" as const,

    onToken(token: string, context: RenderContext): boolean {
      acc += token;

      // 提取 HTML
      const extracted = extractHtml(acc);
      if (extracted.isEmpty) return false;

      // 清理 HTML
      const sanitized = sanitizeHtml(extracted.html);

      // 流式裁剪
      const trimmed = trimForStreaming(sanitized);
      if (!trimmed) return false;

      // 渲染
      if (trimmed === lastCommitted) return true;

      const root = context.rootElement;
      if (!root) return false;

      root.innerHTML = trimmed;
      lastCommitted = trimmed;
      return true;
    },

    onDone(context: RenderContext): void {
      const extracted = extractHtml(acc);
      if (extracted.isEmpty) return;

      const sanitized = sanitizeHtml(extracted.html);
      // flush 不裁剪，保留完整内容

      const root = context.rootElement;
      if (!root) return;

      root.innerHTML = sanitized;
      lastCommitted = sanitized;
    },

    reset(): void {
      acc = "";
      lastCommitted = "";
    },

    getLastRenderedHtml(context: RenderContext): string {
      return lastCommitted;
    },
  };
}

export const innerHtmlStrategy = createInnerHtmlStrategy();