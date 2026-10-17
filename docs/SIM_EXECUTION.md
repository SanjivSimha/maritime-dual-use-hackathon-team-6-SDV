# Deterministic simulator execution

Implementation in progress on baseline PR #35 (`d8bf25d60987b739a9ec53ef15293f48e4376676`).

Public integration seam (Track B only):

- `Runner(initial, *, ..., execution: bool = False)`
- `load_scenario(path, *, decide=None, environment=None, execution: bool = False)`
- `runner.execution_status`: defensive JSON-compatible status with `enabled`, `pending`, `applied`, `blocked`, `crew_gate`.
- Callback `Context.protected: frozenset[str] = frozenset()` and `Context.recovery_confirm: str = "human"`.
- `Event(t_s=runner.next_t_s, kind="operator_confirmation", payload={"by": "deck officer"})` records explicit input at that tick.

Callbacks remain pure. Main must pass context.protected into shedding and context.recovery_confirm into arbitration. Execution is an opt-in orchestrator outside callbacks. Only chosen feasible D3/D4 options execute; alternatives/contingencies and admission installation do not. Execution replay will use an explicit new version; observation bytes remain unchanged. Full semantics, tests and reproduction will follow in this draft.