import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IncrementalHtmlParser, decodeHtmlEntities, tokenize, Token } from '../src/incremental-html-parser';

describe('IncrementalHtmlParser', () => {
  let container: HTMLElement;
  let parser: IncrementalHtmlParser;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    parser = new IncrementalHtmlParser({ rootElement: container });
  });

  // 测试用例将在这里添加

  describe('IncrementalHtmlParser - DOM Applier', () => {
    it('should parse opening tag and create element', () => {
      parser.parse('<div class="test">');
      expect(container.querySelector('div')).toBeTruthy();
      expect(container.querySelector('div')?.getAttribute('class')).toBe('test');
      expect(parser.getTagStack().length).toBe(1);
      expect(parser.getTagStack()[0].tagName).toBe('div');
    });

    it('should parse multiple nested opening tags', () => {
      parser.parse('<div><span><p>');
      expect(container.querySelector('div > span > p')).toBeTruthy();
      expect(parser.getTagStack().length).toBe(3);
    });

    it('should parse self-closing tag without pushing to stack', () => {
      parser.parse('<img src="test.png" />');
      const img = container.querySelector('img');
      expect(img).toBeTruthy();
      expect(img?.getAttribute('src')).toBe('test.png');
      expect(parser.getTagStack().length).toBe(0);
    });

    it('should parse closing tag and pop from stack', () => {
      parser.parse('<div></div>');
      expect(container.querySelector('div')).toBeTruthy();
      expect(parser.getTagStack().length).toBe(0);
    });

    it('should parse nested closing tags correctly', () => {
      parser.parse('<div><span></span></div>');
      expect(container.querySelector('div > span')).toBeTruthy();
      expect(parser.getTagStack().length).toBe(0);
    });

    it('should parse text node and append to current element', () => {
      parser.parse('<div>hello</div>');
      const div = container.querySelector('div');
      expect(div?.textContent).toBe('hello');
    });

    it('should append text to existing text node', () => {
      parser.parse('<div>hello');
      parser.parse('world</div>');
      const div = container.querySelector('div');
      expect(div?.textContent).toBe('helloworld');
    });
  });

  describe('Error handling', () => {
    it('should throw in debug mode on tag mismatch', () => {
      const debugParser = new IncrementalHtmlParser({
        rootElement: container,
        debug: true
      });

      // 应该抛出异常 - closing tag </span> doesn't match open tag </div>
      expect(() => debugParser.parse('<div></span>')).toThrow();
    });

    it('should throw in debug mode when closing tag with empty stack', () => {
      const debugParser = new IncrementalHtmlParser({
        rootElement: container,
        debug: true
      });

      expect(() => {
        debugParser.parse('</div>');
      }).toThrow();
    });

    it('should silently ignore in production mode on tag mismatch', () => {
      const prodParser = new IncrementalHtmlParser({
        rootElement: container,
        debug: false
      });

      prodParser.parse('<div></span>');
      // 应该静默忽略，不抛出异常
      expect(container.querySelector('div')).toBeTruthy();
      expect(prodParser.getTagStack().length).toBe(1);
    });
  });

  it('skeleton test', () => {
    expect(parser).toBeDefined();
    expect(container).toBeDefined();
  });

  it('should handle incremental HTML fragments from example', () => {
    // 第1次增量: <div class="min-h-screen "> <div class="relative overflow-hidden bg-gradient-to-br">
    parser.parse('<div class="min-h-screen ">\n<div class="relative overflow-hidden bg-gradient-to-br">');
    expect(container.querySelector('.min-h-screen')).toBeTruthy();
    expect(container.querySelector('.min-h-screen')?.querySelector('.relative')).toBeTruthy();
    expect(parser.getTagStack().length).toBe(2); // div.min-h-screen, div.relative

    // 第2次增量: add nested div and close it
    parser.parse('\n        <div class="absolute inset-0 opacity-20">\n   </div>');
    expect(container.querySelector('.relative')?.querySelector('.absolute')).toBeTruthy();
    expect(parser.getTagStack().length).toBe(2); // Still 2 (inner div closed)

    // 第3次增量: add span with text
    parser.parse('<span class="inline-block px-3 mb-3">2024年五一假期</span>\n                <h1 class="text-2xl font-bold text-white mb-2">');
    expect(parser.getTagStack().length).toBe(3); // div.min-h-screen, div.relative, h1

    // 第4次增量: close all
    parser.parse('南京旅游攻略</h1>\n                </div></div></div></div>');
    expect(parser.getTagStack().length).toBe(0);
  });

  describe('decodeHtmlEntities', () => {
    it('should decode HTML entities', () => {
      expect(decodeHtmlEntities('&lt;div&gt;')).toBe('<div>');
    });

    it('should decode all common entities', () => {
      expect(decodeHtmlEntities('&lt;')).toBe('<');
      expect(decodeHtmlEntities('&gt;')).toBe('>');
      expect(decodeHtmlEntities('&amp;')).toBe('&');
      expect(decodeHtmlEntities('&quot;')).toBe('"');
      expect(decodeHtmlEntities('&apos;')).toBe("'");
    });

    it('should decode numeric entities', () => {
      expect(decodeHtmlEntities('&#60;')).toBe('<');
      expect(decodeHtmlEntities('&#62;')).toBe('>');
      expect(decodeHtmlEntities('&#39;')).toBe("'");
    });

    it('should decode mixed content', () => {
      expect(decodeHtmlEntities('&lt;div&gt;')).toBe('<div>');
      expect(decodeHtmlEntities('&lt;div class=&quot;test&quot;&gt;')).toBe('<div class="test">');
    });

    it('should return unknown entities unchanged', () => {
      expect(decodeHtmlEntities('&unknown;')).toBe('&unknown;');
    });
  });

  describe('tokenize', () => {
    it('should tokenize opening tag', () => {
      const tokens = tokenize('<div class="test">');
      expect(tokens).toEqual([
        { type: 'opening', tagName: 'div', attributes: 'class="test"' }
      ]);
    });

    it('should tokenize closing tag', () => {
      const tokens = tokenize('</div>');
      expect(tokens).toEqual([
        { type: 'closing', tagName: 'div' }
      ]);
    });

    it('should tokenize self-closing tag', () => {
      const tokens = tokenize('<img src="test.png" />');
      expect(tokens).toEqual([
        { type: 'self-closing', tagName: 'img', attributes: 'src="test.png"' }
      ]);
    });

    it('should tokenize text', () => {
      const tokens = tokenize('hello world');
      expect(tokens).toEqual([
        { type: 'text', content: 'hello world' }
      ]);
    });

    it('should tokenize mixed content', () => {
      const tokens = tokenize('<div>hello</div>');
      expect(tokens).toEqual([
        { type: 'opening', tagName: 'div', attributes: '' },
        { type: 'text', content: 'hello' },
        { type: 'closing', tagName: 'div' }
      ]);
    });

    it('should skip HTML comments', () => {
      const tokens = tokenize('<div><!-- comment --><span></span></div>');
      expect(tokens).toEqual([
        { type: 'opening', tagName: 'div', attributes: '' },
        { type: 'opening', tagName: 'span', attributes: '' },
        { type: 'closing', tagName: 'span' },
        { type: 'closing', tagName: 'div' }
      ]);
    });

    it('should skip single-line comments', () => {
      const tokens = tokenize('<div>// comment\n<span></span></div>');
      expect(tokens).toEqual([
        { type: 'opening', tagName: 'div', attributes: '' },
        { type: 'opening', tagName: 'span', attributes: '' },
        { type: 'closing', tagName: 'span' },
        { type: 'closing', tagName: 'div' }
      ]);
    });

    it('should skip block comments', () => {
      const tokens = tokenize('<div>/* comment */<span></span></div>');
      expect(tokens).toEqual([
        { type: 'opening', tagName: 'div', attributes: '' },
        { type: 'opening', tagName: 'span', attributes: '' },
        { type: 'closing', tagName: 'span' },
        { type: 'closing', tagName: 'div' }
      ]);
    });
  });
});