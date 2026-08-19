# Ask Beacon Agent Orchestration

Ask Beacon is an agentic orchestration layer over deterministic Beacon tools. It does not paste portfolio data into a single prompt, train a model, fine-tune a model, or expose filesystem/Python execution to the model.

## Architecture

```text
User
-> semantic/context resolution
-> tool-capable model adapter
-> model selects allowlisted Beacon tool
-> deterministic tool execution
-> structured observation
-> model decides whether another tool is required
-> validation
-> final grounded answer
```

## Model Adapter

The provider boundary is `ModelAdapter.call(messages, tools) -> ModelResponse`.

Implemented adapters:

- `OpenAIModelAdapter`: lazy provider adapter using `OPENAI_API_KEY` and `OPENAI_MODEL` when available.
- `ScriptedModelAdapter`: deterministic test adapter used by automated tests.

The rest of the agent depends only on `ModelAdapter`, so the provider can be swapped later.

## Tool Loop

`AskBeaconAgent.answer(...)` performs:

1. Resolve user language with the semantic/context layer.
2. Run deterministic preflight triage before tools:
   - `answer`: sufficiently specified or resolvable from UI context
   - `clarify`: materially ambiguous
   - `out_of_scope`: unsupported by the Beacon dataset
   - `unsupported_causality`: can describe what happened but cannot establish why
3. Return clarification before model call when ambiguity is material.
4. Send only user query, resolved context, and tool schemas to the model.
5. Execute only allowlisted Beacon business tools.
6. Append structured tool observations.
7. Repeat up to `MAX_STEPS = 8`.
8. Validate final answer has at least one successful deterministic tool observation and source provenance.

## Safe Events

Safe event types:

- `context_resolved`
- `clarification_requested`
- `tool_selected`
- `tool_completed`
- `calculation_completed`
- `source_verified`
- `validation_failed`
- `answer_completed`
- `out_of_scope`

The log records actions and outcomes, not hidden chain-of-thought.

## Example Simple Execution

Question:

```text
What was BLE's Private Equity allocation versus target in Q3?
```

Expected workflow:

```text
context_resolved
tool_selected: get_asset_allocation
tool_completed
source_verified
answer_completed
```

The final answer is grounded in `get_asset_allocation("BLE", "Q3", "Private Equity")`.

## Example Multi-Step Execution

Question:

```text
What should I investigate about BPT this year?
```

Possible workflow:

```text
get_fund_summary(BPT, FY2026)
get_research_signals(BPT, FY2026)
rank_managers(FY2026, excess return, asc)
get_cash_flows(BPT, FY2026)
```

The model can synthesize supported findings from observations, but must distinguish fact from inference and avoid unsupported causality.

## Failure Handling

- Ambiguous request: returns `status: needs_clarification`.
- Out of scope: returns `status: out_of_scope`.
- Invalid tool args or deterministic tool error: logs `validation_failed`.
- Final answer without tool evidence: rejected with `status: validation_failed`.
- Excessive looping: returns `status: max_steps_exceeded`.

No final UI is implemented yet.

## Ambiguity Examples

`Which manager performed best?`

Ask Beacon does not guess the metric. It asks:

```text
How should I define best performance?

- Highest absolute return
- Highest excess return vs benchmark
- Most consistent outperformer
```

`How did Private Equity do?`

Ask Beacon asks which review lens to use:

```text
- Performance vs benchmark
- Allocation vs policy
- Underlying managers
- Full review
```

`Why did this move?` with BPT / FY2026 / Private Equity context is allowed to proceed using that context.

## Unsupported Causality

For a question like `Why did Manager X underperform?`, Beacon may call manager performance/history tools and state what happened versus benchmark and how consistently. It must also say holdings-level attribution is unavailable and cannot establish the cause.

For strategy-change questions, such as `Why did Manager XYZ change investment strategy?`, Beacon returns `out_of_scope` and offers supported alternatives:

- analyse performance
- compare with benchmark
- show quarterly trend
