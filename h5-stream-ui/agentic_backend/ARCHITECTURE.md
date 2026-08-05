# Agentic UI Generation — Architecture Overview

## Full Pipeline

```mermaid
flowchart TD
    subgraph INPUT[" "]
        USER["👤 User Prompt<br/>(text + data)"]
    end

    subgraph DECISION["Token Budget Check"]
        CHECK{"Input ≤ 50%<br/>of token budget?"}
    end

    subgraph SUMMARIZE["Pass 0: Structural Indexer"]
        SAVE["📁 Save full input<br/>to ContextStore"]
        IDX["🔍 Generate structural index<br/>(LLM call, recursive chunking<br/>if &gt;8K tokens)"]
        CTX_STORE[("📁 ContextStore<br/>(file-based, per session)")]
    end

    subgraph PLAN["Pass 1: Layout Planner"]
        PL_LLM["📐 Plan Agent<br/>(LLM call, ~1200 tok prompt)"]
        PLAN_JSON["LayoutPlan JSON<br/>card_type, sections[],<br/>data_bindings, style_prefs"]
    end

    subgraph COMPOSER["Pass 2: Two-Agent Composer (programmatic)"]
        direction TB
        PARSE["🔧 Parse plan sections<br/>(no LLM — pure code)"]

        subgraph RETRIEVE["Content Retrieval (per section)"]
            R_LOAD["Load full text<br/>from ContextStore"]
            R_CHECK{"Fits in<br/>token budget?"}
            R_SINGLE["Single LLM call<br/>extract data as raw text"]
            R_CHUNK["Recursive chunking<br/>LLM per chunk<br/>concatenate text results"]
        end

        subgraph AGENT_A["Agent A: Page Structure Generator"]
            A_PROMPT["📄 System prompt<br/>(~800 tok, layout rules)"]
            A_LLM["🏗️ LLM call"]
            A_OUT["HTML shell with<br/>&lt;!-- COMP_PLACEHOLDER --&gt;<br/>markers"]
        end

        subgraph AGENT_B["Agent B: Component Generator (per section)"]
            B_CTX["Section spec +<br/>retrieved data +<br/>style context"]
            B_PROMPT["📄 System prompt<br/>(~1200 tok, per-component rules)"]
            B_LLM["🧩 LLM call"]
            B_OUT["HTML fragment<br/>for this component"]
        end

        ASSEMBLE["🔧 Replace placeholders<br/>with component HTML<br/>(regex, no LLM)"]
    end

    subgraph OUTPUT[" "]
        HTML["✅ Final assembled HTML"]
        SSE["📡 SSE stream to frontend<br/>{type: 'token', content: '...'}<br/>{type: 'done'}"]
    end

    subgraph VERIFY["Pass 3: Verification (optional, cloud LLM)"]
        V_SYNTAX["Syntax check"]
        V_STYLE["Style compliance"]
        V_DATA["Data fidelity"]
        V_INTER["Interaction DSL"]
        V_AGG["Aggregate report"]
    end

    subgraph OBSERVABILITY["Observability"]
        DIAG["📊 Response diagnostics<br/>finish_reason, thinking%,<br/>raw content preview"]
        LOG["📝 LLM interaction log<br/>(markdown per session)"]
        DEBUG["🛠️ Debug CLI<br/>--step compose|plan|<br/>page_generate|component_generate"]
    end

    %% Edges
    USER --> CHECK
    CHECK -->|"no, pass through"| PLAN
    CHECK -->|"yes, index first"| SAVE
    SAVE --> CTX_STORE
    SAVE --> IDX
    IDX --> PLAN

    PLAN --> PL_LLM
    PL_LLM --> PLAN_JSON

    PLAN_JSON --> PARSE
    PARSE --> RETRIEVE

    R_LOAD --> CTX_STORE
    R_LOAD --> R_CHECK
    R_CHECK -->|"yes"| R_SINGLE
    R_CHECK -->|"no"| R_CHUNK
    R_SINGLE --> AGENT_B
    R_CHUNK --> AGENT_B

    PARSE --> AGENT_A
    A_PROMPT --> A_LLM
    A_LLM --> A_OUT

    A_OUT --> ASSEMBLE
    B_CTX --> B_PROMPT
    B_PROMPT --> B_LLM
    B_LLM --> B_OUT
    B_OUT --> ASSEMBLE

    ASSEMBLE --> HTML
    HTML --> SSE
    HTML -.->|"if verification enabled"| VERIFY
    VERIFY -.-> SSE

    PL_LLM -.-> DIAG
    A_LLM -.-> DIAG
    B_LLM -.-> DIAG
    R_SINGLE -.-> DIAG
    R_CHUNK -.-> DIAG
    IDX -.-> DIAG

    PL_LLM -.-> LOG
    A_LLM -.-> LOG
    B_LLM -.-> LOG
    R_SINGLE -.-> LOG
    DEBUG -.-> LOG

    DEBUG -.-> PLAN
    DEBUG -.-> COMPOSER
    DEBUG -.-> VERIFY

    style INPUT fill:#e8f4fd
    style OUTPUT fill:#e8f5e9
    style COMPOSER fill:#fff3e0
    style OBSERVABILITY fill:#f3e5f5
    style CTX_STORE fill:#ffecb3
```

## LLM Call Breakdown

| # | Step | Agent | Tokens (sys+usr) | Output | Thinking |
|---|------|-------|------------------|--------|----------|
| 0 | Summarize (if needed) | Indexer | ~1,200 + content | ~200-500 tok structural index | Disabled |
| 1 | Plan | Layout Planner | ~1,200 + query | JSON (card_type, sections, etc.) | Disabled |
| 2 | Page Shell | Agent A | ~800 + plan JSON | HTML with placeholders | Disabled |
| 3-N | Per Component | Agent B | ~1,200 + data+style | HTML fragment | Disabled |
| * | Per Section Retrieval | Content Retriever | ~200 + context | Raw text (field: value list) | Disabled |
| * | Verification (opt) | Cloud LLM | Full original prompts | Pass/fail report | N/A |

## Thinking Mode Control

```
thinking_enabled=True  →  reasoning: {"effort": "low/medium/high"}  →  <think> tags or reasoning field
thinking_enabled=False →  reasoning: {"effort": "none"}             →  no reasoning, direct output
```

**Default**: All local LLM calls disable reasoning (`thinking_enabled=False`) to avoid burning output tokens on
`<think>` blocks. Cloud LLM calls keep the default (`True`).


**/no_think injection (Qwen3)**: when thinking is disabled and the model is Qwen3, `LlmClient._apply_no_think` prepends `/no_think` to the system prompt (gated by `NO_THINK_ENABLED`, text via `NO_THINK_DIRECTIVE` env vars; Qwen3 detected from `LOCAL_LLM_MODEL`). Any reasoning block that leaks through despite the directive is stripped by `_strip_thinking_measured`, which also tallies the wasted thinking tokens, exposed via the `last_thinking_tokens` property and warned about in the logs.

## Response Diagnostics (every LLM call)

```
─ RESPONSE DIAGNOSTICS ─────────────────────────────────
  Model:            qwen3:8b
  Finish reason:    stop ✅
  Raw content:      2430 chars  (~607 tokens)
  Reasoning field:  none
  Max tokens req:   4096
  API prompt tok:   1850  (our estimate: 1920)
  API compl tok:    607
  Thinking/overhead: 0% of total output
```

Watches for: `finish_reason=length` (truncation), unclosed `<think>` tags, `reasoning` field in stream,
thinking-dominant responses (&gt;70% overhead), and empty responses.

## File Map

```
agentic_backend/
├── server.py                          # FastAPI + SSE endpoints
├── debug_cli.py                       # Per-step debug tool
├── app/
│   ├── config.py                      # AppConfig, LlmConfig (env vars)
│   ├── shared/
│   │   └── llm_client.py              # LlmClient (OpenAI-compatible, diagnostics, retries)
│   ├── generation/
│   │   ├── orchestrator.py            # Plan → Composer pipeline
│   │   ├── plan.py                    # Layout plan generation + validate_plan()
│   │   ├── composer.py                # Programmatic two-agent coordinator
│   │   ├── page_generator.py          # Agent A: HTML shell with placeholders
│   │   ├── component_generator.py     # Agent B: per-component HTML fragments
│   │   ├── content_retriever.py       # LLM-based data retrieval (recursive chunking)
│   │   ├── generate.py                # Legacy monolithic generate (fallback)
│   │   ├── llm_client.py              # GenerationLlmClient (wrapper, thinking=off)
│   │   └── prompts/                    # grouped per step; system + user templates
│   │       ├── summarize/summarize_system.md
│   │       ├── plan/{plan_system.md, plan_jsonl.md, plan_user.md, plan_feedback.md}
│   │       ├── page_generate/{page_generate_system.md, page_generate_user.md}
│   │       ├── component_generate/{component_generate_system.md, component_user.md}
│   │       ├── content_retrieve/content_retrieve_system.md
│   │       └── generate/{generate_system.md, generate_user.md}   # legacy fallback
│   ├── prompts/
│   │   ├── registry.py                # Step → prompt file mappings (uses subdir paths)
│   │   └── loader.py                  # PromptLoader: load_for_step (system) + load_raw (templates)
│   ├── verification/
│   │   └── verifier.py                # Cloud-based verification
│   └── utils/
│       ├── summarizer.py              # Structural indexer (Pass 0)
│       ├── context_store.py           # File-based full-input storage
│       ├── llm_logger.py              # Markdown interaction logs
│       └── token_counter.py           # tiktoken / heuristic counter
```
