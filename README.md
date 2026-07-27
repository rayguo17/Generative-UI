# generative-UI

一个用于 **LLM 流式生成 H5 界面并实时预览** 的实验项目。

当前主工程在 `h5-stream-ui`，包含三部分：
- `backend`：FastAPI + OpenAI-compatible 流式后端
- `frontend`：Vite + TypeScript 预览前端（RAW 输出 + Preview）
- `harmony-web-chat`：HarmonyOS 原生聊天壳 + 内嵌 Web 渲染页（消费同一后端流式接口）

## 目录结构

```text
generative-UI/
├─ h5-stream-ui/
│  ├─ backend/
│  │  ├─ server.py
│  │  ├─ prompt_loader.py
│  │  ├─ stream_cli.py
│  │  ├─ requirements.txt
│  │  └─ .env
│  ├─ frontend/
│  │  ├─ src/
│  │  ├─ package.json
│  │  └─ ...
│  ├─ prompts/
│  │  └─ *.md
│  └─ harmony-web-chat/
│     ├─ entry/
│     │  ├─ src/main/ets/pages/Index.ets
│     │  └─ src/main/resources/rawfile/chat_renderer.html
│     └─ ...
```

## 功能概览

- 单输入框 query（可混合描述和数据）
- LLM 流式输出（SSE）
- 右侧 Preview 渲染（非整页重建，增量更新）
- 设备视图切换：全宽 / 直屏 / 双折叠 / 三折叠平板
- RAW 复制、Preview 导出 `.html`
- 交互 DSL（`data-interactions`，如 `setPage` / `openUrl`）
- 页面内模型配置（BASE_URL / API_KEY / MODEL）

## Prompt 套件（`h5-stream-ui/prompts/`）

生成行为主要由 **`*.md` 分片** 组成 system prompt（由 `backend/prompt_loader.py` 等加载）。**修改任意 `prompts/` 文件后请重启 `python server.py`**，否则仍沿用旧文案。

维护时可优先查阅：

| 主题 | 文件 |
|------|------|
| 输入权威、**可见文案 / URL / CTA 须绑定数据源**（禁止臆造「添加…」等） | `02-input-handling.md`、`08-special-data-processing.md` §6、§8 自检 |
| **装饰底图**（全卡 `inset-0` 默认 vs 仅底条）、可见性与叠层 | `08-special-data-processing.md` §2.3、`07-harmony-static-style-spec.md` §7.1 |
| Tailwind 用法、预览壳约束 | `04-tailwind-and-stack.md` |
| 输出形态（仅 HTML fragment） | `03-output-format.md` |

## 环境要求

- Python 3.10+
- Node.js 18+

## 快速启动

### 1) 启动后端

```bash
cd h5-stream-ui/backend
cp .env.example .env
pip install -r requirements.txt
python server.py
```

默认监听：`http://127.0.0.1:8765`

### 2) 启动前端

```bash
cd h5-stream-ui/frontend
npm install
npm run dev
```

默认地址：`http://127.0.0.1:5173`

前端通过 Vite 代理访问后端 `/api/generate` 和 `/health`。

### 3) 启动 HarmonyOS 客户端（可选）

`h5-stream-ui/harmony-web-chat` 是原生聊天界面，内部通过 ArkWeb 加载本地 `chat_renderer.html`，并请求同一个后端 `/api/generate`。

```bash
# 在 DevEco Studio 中打开 h5-stream-ui/harmony-web-chat 工程并运行 entry 模块
```

首次运行请在客户端右上角「模型设置」里确认后端地址（例如：
- 本机调试：`http://127.0.0.1:8765`
- 局域网设备调试：`http://<你的电脑局域网IP>:8765`
）

## 后端环境变量（`h5-stream-ui/backend/.env`）

最小必填：

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
OPENAI_MODEL=...
HOST=127.0.0.1
PORT=8765
```

说明：
- `OPENAI_BASE_URL` 为 OpenAI-compatible 地址（如 GLM、OneAPI、自建网关等）
- `OPENAI_MODEL` 为默认模型名（当前前端不填模型时使用）

## 页面内模型配置说明

右上角 `模型配置` 可填写：
- `BASE_URL`
- `API_KEY`
- `MODEL`

保存后会写入浏览器本地存储，并在请求时优先传给后端。  
后端优先级：
1. 前端传入
2. `.env` 默认值

## 常见问题

- **Q: 为什么会出现很长等待后才出 token？**  
  A: 可能是上游模型首包慢，或模型先生成了被过滤的非 HTML 前缀。后端日志里可看 `stream_timing`。

- **Q: 为什么要求只输出 HTML？**  
  A: 当前预览链路按 HTML fragment 渲染，非 HTML 前缀会影响流式显示与稳定性。

- **Q: 部分模型会输出 `<think>` 怎么办？**  
  A: 后端已对流式结果做 `<think>...</think>` 过滤，并且仅在命中白名单根标签（如 `<div>/<main>/<section>/<article`）后才开始转发渲染。

- **Q: 预览高度为什么会变？**  
  A: 预览按内容高度自适应，并受每个设备模式的最大高度限制；超出时显示竖向滚动。

## 开发建议

- 修改 **`prompts/*.md`** 后：**重启后端**；需要时用 `stream_cli.py` 快速验证输出形态
- 前端改动后先 `npm run build`，避免 dev 环境掩盖类型错误
- 后端改动后可运行：

```bash
python -m py_compile backend/server.py
```
