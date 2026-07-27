export interface IncrementalHtmlParserOptions {
  rootElement: HTMLElement;
  debug?: boolean;
}

export type Token =
  | { type: 'opening'; tagName: string; attributes: string }
  | { type: 'closing'; tagName: string }
  | { type: 'self-closing'; tagName: string; attributes: string }
  | { type: 'text'; content: string }
  | { type: 'comment'; content: string };

// 注释正则表达式
const htmlCommentRegex = /^<!--[\s\S]*?-->/;
const lineCommentRegex = /^\/\/.*/;
const blockCommentRegex = /^\/\*[\s\S]*?\*\//;
const templateCommentRegex = /^\{#[\s\S]*?#\}/;

export function tokenize(html: string): Token[] {
  const tokens: Token[] = [];
  let remaining = html;

  // 正则表达式
  const openingTagRegex = /^<([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>/;
  const closingTagRegex = /^<\/([a-zA-Z][a-zA-Z0-9-]*)>/;
  const selfClosingTagRegex = /^<([a-zA-Z][a-zA-Z0-9-]*)([^>]*)\/>/;

  while (remaining.length > 0) {
    // 跳过空白
    if (/^\s+/.test(remaining)) {
      const match = remaining.match(/^\s+/);
      if (match) remaining = remaining.slice(match[0].length);
      continue;
    }

    // 跳过 HTML 注释 <!-- -->
    const htmlCommentMatch = remaining.match(htmlCommentRegex);
    if (htmlCommentMatch) {
      remaining = remaining.slice(htmlCommentMatch[0].length);
      continue;
    }

    // 跳过单行注释 //
    const lineCommentMatch = remaining.match(lineCommentRegex);
    if (lineCommentMatch) {
      remaining = remaining.slice(lineCommentMatch[0].length);
      continue;
    }

    // 跳过块注释 /* */
    const blockCommentMatch = remaining.match(blockCommentRegex);
    if (blockCommentMatch) {
      remaining = remaining.slice(blockCommentMatch[0].length);
      continue;
    }

    // 跳过模板注释 {# #}
    const templateCommentMatch = remaining.match(templateCommentRegex);
    if (templateCommentMatch) {
      remaining = remaining.slice(templateCommentMatch[0].length);
      continue;
    }

    // 尝试匹配自闭合标签
    const selfClosingMatch = remaining.match(selfClosingTagRegex);
    if (selfClosingMatch) {
      tokens.push({
        type: 'self-closing',
        tagName: selfClosingMatch[1].toLowerCase(),
        attributes: selfClosingMatch[2].trim()
      });
      remaining = remaining.slice(selfClosingMatch[0].length);
      continue;
    }

    // 尝试匹配开标签
    const openingMatch = remaining.match(openingTagRegex);
    if (openingMatch) {
      tokens.push({
        type: 'opening',
        tagName: openingMatch[1].toLowerCase(),
        attributes: openingMatch[2].trim()
      });
      remaining = remaining.slice(openingMatch[0].length);
      continue;
    }

    // 尝试匹配闭标签
    const closingMatch = remaining.match(closingTagRegex);
    if (closingMatch) {
      tokens.push({
        type: 'closing',
        tagName: closingMatch[1].toLowerCase()
      });
      remaining = remaining.slice(closingMatch[0].length);
      continue;
    }

    // 匹配文本内容
    const textMatch = remaining.match(/^[^<]+/);
    if (textMatch) {
      const text = textMatch[0];
      if (text.trim()) {
        tokens.push({ type: 'text', content: text });
      }
      remaining = remaining.slice(text.length);
      continue;
    }

    // 无法匹配，跳过剩余内容
    break;
  }

  return tokens;
}

interface TagStackItem {
  tagName: string;
  element: HTMLElement;
}

export function decodeHtmlEntities(text: string): string {
  const entities: Record<string, string> = {
    '&lt;': '<',
    '&gt;': '>',
    '&amp;': '&',
    '&quot;': '"',
    '&apos;': "'",
    '&#39;': "'",
  };

  return text.replace(/&[a-zA-Z]+;|&#\d+;/g, (match) => {
    if (entities[match]) return entities[match];
    // Handle numeric entities &#123;
    const numMatch = match.match(/^&#(\d+);$/);
    if (numMatch) {
      return String.fromCharCode(parseInt(numMatch[1], 10));
    }
    return match;
  });
}

function parseAttributes(attributesStr: string): Record<string, string> {
  const attributes: Record<string, string> = {};
  if (!attributesStr) return attributes;

  const attrRegex = /([a-zA-Z][a-zA-Z0-9-]*)="([^"]*)"/g;
  let match;
  while ((match = attrRegex.exec(attributesStr)) !== null) {
    attributes[match[1]] = match[2];
  }

  return attributes;
}

export class IncrementalHtmlParser {
  private rootElement: HTMLElement;
  private tagStack: TagStackItem[] = [];
  private debug: boolean = false;

  constructor(options: IncrementalHtmlParserOptions) {
    this.rootElement = options.rootElement;
    this.debug = options.debug ?? false;
  }

  parse(fragment: string): void {
    const tokens = tokenize(fragment);

    for (const token of tokens) {
      switch (token.type) {
        case 'opening':
          this.handleOpeningTag(token.tagName, token.attributes);
          break;
        case 'closing':
          this.handleClosingTag(token.tagName);
          break;
        case 'self-closing':
          this.handleSelfClosingTag(token.tagName, token.attributes);
          break;
        case 'text':
          this.handleText(token.content);
          break;
      }
    }
  }

  private handleOpeningTag(tagName: string, attributes: string): void {
    const element = document.createElement(tagName);
    const attrs = parseAttributes(attributes);

    for (const [key, value] of Object.entries(attrs)) {
      element.setAttribute(key, value);
    }

    // 挂载到栈顶元素或根元素
    const parent = this.tagStack.length > 0
      ? this.tagStack[this.tagStack.length - 1].element
      : this.rootElement;

    parent.appendChild(element);

    // 入栈
    this.tagStack.push({ tagName, element });
  }

  private handleSelfClosingTag(tagName: string, attributes: string): void {
    const element = document.createElement(tagName);
    const attrs = parseAttributes(attributes);

    for (const [key, value] of Object.entries(attrs)) {
      element.setAttribute(key, value);
    }

    const parent = this.tagStack.length > 0
      ? this.tagStack[this.tagStack.length - 1].element
      : this.rootElement;

    parent.appendChild(element);
  }

  private handleClosingTag(tagName: string): void {
    // 查找栈中匹配的标签
    let tagIndex = -1;
    for (let i = this.tagStack.length - 1; i >= 0; i--) {
      if (this.tagStack[i].tagName === tagName) {
        tagIndex = i;
        break;
      }
    }

    if (tagIndex === -1) {
      // 栈中没有匹配的标签
      if (this.debug) {
        throw new Error(`Closing tag </${tagName}> does not match any open tag`);
      }
      return; // 生产模式静默忽略
    }

    // 弹出栈顶到匹配标签的所有元素
    this.tagStack = this.tagStack.slice(0, tagIndex);
  }

  private handleText(content: string): void {
    if (this.tagStack.length === 0) return; // 没有打开的标签，静默忽略

    const parent = this.tagStack[this.tagStack.length - 1].element;
    const decodedContent = decodeHtmlEntities(content);

    // 尝试合并到最后一个文本节点
    const lastChild = parent.lastChild;
    if (lastChild && lastChild.nodeType === Node.TEXT_NODE) {
      (lastChild as Text).textContent += decodedContent;
    } else {
      const textNode = document.createTextNode(decodedContent);
      parent.appendChild(textNode);
    }
  }

  getTagStack(): TagStackItem[] {
    return this.tagStack;
  }

  reset(): void {
    this.tagStack = [];
    this.rootElement.innerHTML = '';
  }
}