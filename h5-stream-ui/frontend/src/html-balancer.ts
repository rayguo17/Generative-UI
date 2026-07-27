/**
 * HTML 标签平衡器 - 使用原生 DOMParser 检测并提取完整 HTML
 * 用于流式渲染场景，处理未闭合的 HTML 标签
 *
 * 改进：只缓存无法解析的尾部，让能解析的部分先显示
 */

export interface ParseResult {
  nodes: Node[];              // 完整节点列表
  consumedLength: number;     // 已消费的字符数
  hasUnclosedTag: boolean;    // 是否还有未闭合标签
}

export class HtmlBalancer {
  private buffer: string = "";
  private lastConsumedIndex: number = 0;

  /**
   * 接收新的 HTML 片段，尝试解析出完整节点
   * @param chunk 新收到的 HTML 片段
   * @returns 解析结果，包含完整节点或 null（需继续累积）
   */
  feed(chunk: string): ParseResult | null {
    this.buffer += chunk;

    // 尝试分段解析：只保留无法解析的尾部
    const result = this.tryParseIncremental();
    if (!result) {
      return null;
    }

    // 检查是否实际产出了新节点
    if (result.nodes.length === 0 && result.consumedLength === 0) {
      return null;
    }

    return result;
  }

  /**
   * 分段解析 - 尝试从 buffer 中解析出完整部分
   */
  private tryParseIncremental(): ParseResult | null {
    // 找到最后一个可能完整的 HTML 结束位置
    let splitPoint = this.findLastCompletePoint(this.buffer);

    // 如果没有找到可解析的部分，返回 null 继续累积
    if (splitPoint <= 0) {
      return null;
    }

    const contentToParse = this.buffer.slice(0, splitPoint);
    const remaining = this.buffer.slice(splitPoint);

    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(contentToParse, "text/html");

      const body = doc.body;
      if (!body || body.childNodes.length === 0) {
        // 解析结果为空，可能是中间状态，继续累积
        return null;
      }

      // 成功解析，更新 buffer 为剩余部分
      const nodes = Array.from(body.childNodes);
      const consumedLength = splitPoint;
      this.lastConsumedIndex = consumedLength;
      this.buffer = remaining;

      return {
        nodes,
        consumedLength,
        hasUnclosedTag: remaining.length > 0,
      };
    } catch {
      return null;
    }
  }

  /**
   * 找到最后一个可能完整的 HTML 结束位置
   * 扫描缓冲区，找到可以安全解析的最后一个完整位置
   */
  private findLastCompletePoint(buffer: string): number {
    const trimmed = buffer.trimEnd();
    if (trimmed.length === 0) return 0;

    const lastLt = trimmed.lastIndexOf("<");
    const lastGt = trimmed.lastIndexOf(">");

    // 没有 '<'，说明没有标签，整个内容都是完整的
    if (lastLt === -1) {
      return trimmed.length;
    }

    // 有 '<' 但没有 '>'，或 '<' 在 '>' 后面 = 有未闭合标签
    // 从最后一个 '<' 位置之前分割，这样 <div>abc< 就保留 <div>abc
    if (lastGt === -1 || lastLt > lastGt) {
      // 末尾有不完整标签，从 < 之前分割
      return lastLt;
    }

    // 检查是否在引号中间
    if (this.isInsideAttribute(trimmed)) {
      // 在引号中间，需要找到引号闭合
      const lastOpenQuote = Math.max(
        trimmed.lastIndexOf('"'),
        trimmed.lastIndexOf("'")
      );
      const afterQuote = trimmed.slice(lastOpenQuote + 1);
      const nextLt = afterQuote.indexOf("<");
      const nextGt = afterQuote.indexOf(">");

      if (nextLt !== -1 && (nextGt === -1 || nextLt < nextGt)) {
        // 属性值中间还有标签未闭合，从下一个 < 之前分割
        return trimmed.length - afterQuote.length + lastOpenQuote + 1;
      }
    }

    // 末尾可能有文本，从最后一个 > 之后检查
    if (lastGt < trimmed.length - 1) {
      const afterLastGt = trimmed.slice(lastGt + 1);
      // 检查末尾是否有未闭合标签
      const nextLt = afterLastGt.indexOf("<");
      if (nextLt !== -1) {
        // 末尾有不完整标签，从下一个 < 之前分割
        return lastGt + 1 + nextLt;
      }
    }

    return trimmed.length;
  }

  /**
   * 检测是否在引号中间
   */
  private isInsideAttribute(trimmed: string): boolean {
    const lastOpenQuote = Math.max(
      trimmed.lastIndexOf('"'),
      trimmed.lastIndexOf("'")
    );
    if (lastOpenQuote === -1) return false;

    const afterQuote = trimmed.slice(lastOpenQuote + 1);
    // 检查引号后面是否有 > 或 < 在引号外面
    const hasCloseQuote = afterQuote.includes('"') || afterQuote.includes("'");
    if (!hasCloseQuote) {
      return true;
    }

    // 检查是否有其他未闭合的引号
    let inString = false;
    let inAttr = false;
    for (let i = trimmed.length - 1; i >= 0; i--) {
      const c = trimmed[i];
      if (c === '"' || c === "'") {
        if (!inString) {
          inString = true;
        } else if (inAttr) {
          inAttr = false;
          inString = false;
        }
      } else if (c === ">" && inString) {
        inAttr = true;
      } else if (c === "<" && inString && !inAttr) {
        return true;
      }
    }

    return false;
  }

  /**
   * 强制解析当前缓冲区（流结束或超时情况下使用）
   * @returns 尽可能解析出的节点（可能包含不完整的最后节点）
   */
  flush(): ParseResult {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(this.buffer, "text/html");
      const nodes = Array.from(doc.body.childNodes);
      const consumedLength = this.buffer.length;

      this.buffer = "";
      this.lastConsumedIndex = consumedLength;

      return {
        nodes,
        consumedLength,
        hasUnclosedTag: false,
      };
    } catch {
      // 解析失败，返回空结果
      return {
        nodes: [],
        consumedLength: this.buffer.length,
        hasUnclosedTag: true,
      };
    }
  }

  /**
   * 获取当前缓冲区内容（用于调试）
   */
  getBuffer(): string {
    return this.buffer;
  }

  /**
   * 重置缓冲区
   */
  reset(): void {
    this.buffer = "";
    this.lastConsumedIndex = 0;
  }
}