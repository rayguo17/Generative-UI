/**
 * Streaming HTML Extractor
 *
 * 流式 HTML 文本提取器，支持三种输入模式：
 * 1. JSON 块: {"html": "<div>...</div>"}
 * 2. Markdown fence: ```html...```
 * 3. Raw HTML: <div>...</div>
 *
 * 模式探测只发生一次，互斥：
 * - 首次调用检测 JSON 模式或 HTML 模式
 * - HTML 模式中若发现 `<`，确定是 HTML，不再切换
 * - JSON 模式结束后，后续输入全部忽略
 */

export interface ExtractionResult {
  html: string;
  hasNewContent: boolean;
}

type ExtractMode = "pending" | "json" | "html" | "done";

export class StreamingHtmlExtractor {
  private buffer = "";
  private mode: ExtractMode = "pending";
  private firstHtmlFragment = true;
  private prevJsonLen = 0;
  // Cached JSON string start position (constant after mode detection)
  private jsonStart = 0;
  // Decode cursor: position in buffer to start decoding from
  private jsonCursor = 0;

  extract(token: string): ExtractionResult {
    if (this.mode === "done") {
      return { html: "", hasNewContent: false };
    }

    this.buffer += token;

    // Strip fence markers in both modes
    this.buffer = this.buffer
      .replace(/^```[a-zA-Z]*\s*/, "")
      .replace(/\s*```.*$/, "")
      .trimStart();

    if (!this.buffer) {
      return { html: "", hasNewContent: false };
    }

    if (this.mode === "pending") {
      const trimmed = this.buffer.trimStart();
      if (trimmed.startsWith("{")) {
        const start = findHtmlJsonStringStart(trimmed);
        if (start !== null) {
          this.mode = "json";
          this.jsonStart = start;
          this.jsonCursor = start;
          return this.extractJson();
        }
      }
      this.mode = "html";
    }

    if (this.mode === "json") {
      return this.extractJson();
    }

    // === HTML mode ===
    return this.extractHtml();
  }

  private extractJson(): ExtractionResult {
    // When buffer is purely buffered unclosed tag content, start from 0
    // Otherwise use cached jsonCursor (updated after buffering)
    const start = this.buffer.startsWith("<") ? 0 : this.jsonCursor;
    const { text, endIdx } = decodeJsonString(this.buffer, start);

    if (endIdx !== null) {
      // JSON confirmed closed — drain trailing, mark done
      this.buffer = this.buffer
        .slice(endIdx + 1)
        .replace(/^}/, "")
        .replace(/^\s+/, "");
      this.mode = "done";
      // Return full decoded text (reset prev since JSON is complete)
      this.prevJsonLen = 0;
      return { html: text, hasNewContent: !!text.trim() };
    }

    // Not closed yet — check for unclosed HTML tag at end
    const lastLt = text.lastIndexOf("<");
    if (lastLt > 0 && lastLt < text.length - 1) {
      const lastGt = text.lastIndexOf(">");
      if (lastGt < lastLt) {
        // Unclosed <...> at end — output complete part, buffer the tag
        const newText = text.slice(0, lastLt);
        this.buffer = text.slice(lastLt);
        this.prevJsonLen = newText.length;
        return { html: newText, hasNewContent: !!newText.trim() };
      }
    }

    // Stopped at unclosed " — buffer the quote for next call
    const newLen = text.length;
    if (newLen > this.prevJsonLen) {
      const incremental = text.slice(this.prevJsonLen);
      this.prevJsonLen = newLen;
      this.buffer = '"';
      this.jsonCursor = 1;
      return { html: incremental, hasNewContent: !!incremental.trim() };
    }
    this.prevJsonLen = newLen;
    this.buffer = '"';
    this.jsonCursor = 1;
    return { html: "", hasNewContent: false };
  }

  private extractHtml(): ExtractionResult {
    const firstLtIdx = this.buffer.indexOf("<");
    const lastGtIdx = this.buffer.lastIndexOf(">");

    if (firstLtIdx >= 0 && lastGtIdx >= 0) {
      const hasOpeningTagAfterLastGt = this.buffer.slice(lastGtIdx + 1).indexOf("<") >= 0;
      const htmlStartIdx = this.firstHtmlFragment ? firstLtIdx : 0;

      if (hasOpeningTagAfterLastGt) {
        const html = this.buffer.slice(htmlStartIdx, lastGtIdx + 1).trim();
        this.buffer = this.buffer.slice(lastGtIdx + 1);
        this.firstHtmlFragment = false;
        return { html, hasNewContent: !!html };
      } else {
        const html = this.buffer.slice(htmlStartIdx).trim();
        this.buffer = "";
        this.firstHtmlFragment = false;
        return { html, hasNewContent: !!html };
      }
    }

    if (this.firstHtmlFragment) {
      return { html: "", hasNewContent: false };
    }

    return { html: this.buffer.trim(), hasNewContent: !!this.buffer.trim() };
  }

  reset(): void {
    this.buffer = "";
    this.mode = "pending";
    this.firstHtmlFragment = true;
    this.prevJsonLen = 0;
    this.jsonStart = 0;
    this.jsonCursor = 0;
  }
}

export function createStreamingHtmlExtractor(): StreamingHtmlExtractor {
  return new StreamingHtmlExtractor();
}

// ─── JSON decoding helpers ────────────────────────────────────────────────

function findHtmlJsonStringStart(s: string): number | null {
  const m = /\{\s*"html"\s*:\s*"/.exec(s);
  return m ? m.index + m[0].length : null;
}

function lookaheadJsonClose(s: string, quoteIdx: number): boolean {
  let j = quoteIdx + 1;
  while (j < s.length && s[j] === " ") j++;
  if (j >= s.length) return false;
  const c = s[j]!;
  return c === "}" || c === "]" || c === ",";
}

function decodeJsonString(
  s: string,
  start: number,
): { text: string; endIdx: number | null } {
  let out = "";
  let i = start;
  while (i < s.length) {
    const c = s[i]!;
    if (c === '"') {
      if (lookaheadJsonClose(s, i)) {
        return { text: out, endIdx: i };
      }
      // Not confirmed as JSON close — treat as regular character
    }
    if (c === "\\") {
      if (i + 1 >= s.length) {
        return { text: out, endIdx: null };
      }
      const n = s[i + 1]!;
      if (n === '"' || n === "\\" || n === "/") {
        out += n;
        i += 2;
        continue;
      }
      if (n === "b") { out += "\b"; i += 2; continue; }
      if (n === "f") { out += "\f"; i += 2; continue; }
      if (n === "n") { out += "\n"; i += 2; continue; }
      if (n === "r") { out += "\r"; i += 2; continue; }
      if (n === "t") { out += "\t"; i += 2; continue; }
      if (n === "u") {
        if (i + 6 > s.length) return { text: out, endIdx: null };
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
  return { text: out, endIdx: null };
}