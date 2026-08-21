# Ask Beacon LangGraph + Ollama

Ask Beacon uses the existing LangGraph conversation flow with a local Ollama model provider.

## Runtime Flow

```text
Ask Beacon UI
-> POST /api/ask-beacon
-> LangGraph thread
-> ChatOllama
-> trusted Beacon Python tools
-> canonical Python calculations
-> normalized workbook-derived data
-> workbook provenance
```

The endpoint contract remains:

```json
{
  "thread_id": "thread_123",
  "message": "What was BPT's FY2026 return?",
  "application_context": {
    "fund": "BPT",
    "period": "FY2026"
  }
}
```

`thread_id` is persisted by the LangGraph SQLite checkpointer:

```text
data/runtime/ask_beacon_checkpoints.sqlite
```

## Local Model

The default local model is:

```text
qwen3:1.7b
```

Override it with:

```text
OLLAMA_MODEL=qwen3:1.7b
```

The adapter uses Ollama's local endpoint:

```text
http://localhost:11434
```

## Data Rules

The model owns natural-language interpretation, conversational clarification, tool selection, and final wording.

Deterministic Beacon Python services own financial calculations, filtering, ranking, period logic, reconciliation, and provenance.

Research signals may guide investigation, but numerical facts must come from canonical Beacon tools.
