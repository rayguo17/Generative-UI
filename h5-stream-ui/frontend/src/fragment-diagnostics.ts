/**
 * 片段诊断器 - 检测 HTML 片段中的安全问题
 *
 * 返回问题描述列表，用于警告用户
 */

export function collectFragmentDiagnostics(fragment: string): string[] {
  const issueSet = new Set<string>();
  if (!fragment.trim()) return [];
  if (/\son[a-z]+\s*=/i.test(fragment)) {
    issueSet.add("包含内联事件属性（onerror/onclick 等）");
  }
  const dataInteractions = [...fragment.matchAll(/data-interactions\s*=\s*(['"])([\s\S]*?)\1/gi)];
  for (const m of dataInteractions) {
    const raw = m[2] ?? "";
    try {
      const parsed = JSON.parse(raw) as { onClick?: Array<{ type?: string; params?: Record<string, unknown> }> };
      const onClick = Array.isArray(parsed.onClick) ? parsed.onClick : [];
      for (const action of onClick) {
        if (action?.type !== "openUrl") continue;
        const url = action.params?.url;
        if (typeof url === "string" && !url.startsWith("https://")) {
          issueSet.add("openUrl 不是 https（宿主会拦截）");
          break;
        }
      }
    } catch {
      issueSet.add("data-interactions 不是合法 JSON");
      break;
    }
  }
  return [...issueSet];
}