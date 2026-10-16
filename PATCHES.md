# Точечные патчи (🔎 не прогонялись на вашем репо — применяйте с `pytest`)

## A1 benchmarking.py — уважать `case["mode"]`
```diff
-def run_benchmark(config, *, model=None, mode: str = "medium", ...
+def run_benchmark(config, *, model=None, mode: str | None = None, ...
@@
-                    answer = Orchestrator(config, project, backend).answer(case["query"], mode=mode, budget_tokens=budget_tokens)
-                    result = {"case_id": case["id"], "repeat": repeat, "mode": mode, ...
+                    case_mode = mode or case.get("mode", "medium")      # CLI --mode forces, otherwise per-case
+                    answer = Orchestrator(config, project, backend).answer(case["query"], mode=case_mode, budget_tokens=budget_tokens)
+                    result = {"case_id": case["id"], "repeat": repeat, "mode": case_mode, ...
```
cli.py: `run.add_argument("--mode", choices=[...], default=None)`; `args.mode` передаётся как есть.

## A2 pyproject.toml
```diff
-atomic_flow = ["benchmark_data/*.json", "benchmark_data/fixtures/*.py"]
+atomic_flow = ["benchmark_data/*.json", "benchmark_data/fixtures/**/*.py"]
```

## A3 api/server.py
```diff
-app = create_app()
+def __getattr__(name):            # `uvicorn atomic_flow.api.server:app` still works, import stays side-effect free
+    if name == "app":
+        return create_app()
+    raise AttributeError(name)
```
cli.py `cmd_up`: `uvicorn.run("atomic_flow.api.server:create_app", factory=True, host=..., port=..., reload=False)`.

## A10/A8 activate.py — синонимы только в запросе, вес 0.5, не в статистике matched
```diff
-from atomic_flow.activation.text import raw_terms, terms
+from atomic_flow.activation.text import raw_terms, synonyms_for, terms
@@ def search(...):
-        q_terms = list(dict.fromkeys(terms(text)))
+        primary = list(dict.fromkeys(terms(text)))
+        q_weight = {t: 1.0 for t in primary}
+        for t in primary:
+            for s in synonyms_for(t):
+                q_weight.setdefault(s, 0.5)
+        q_terms = list(q_weight)
@@
-        for term in q_terms:
+        for term, qw in q_weight.items():
@@
-                scores[i] += idf * weight * (K1 + 1) / (...)
+                scores[i] += qw * idf * weight * (K1 + 1) / (...)
@@
-        matched = sum(1 for t in q_terms if t in top_terms)
-        ratio = matched / len(q_terms) if q_terms else 0.0
+        matched = sum(1 for t in primary if t in top_terms)
+        ratio = matched / len(primary) if primary else 0.0
```
Индекс (`ActivationIndex.__init__`) продолжает звать `terms(text)` — теперь без синонимов.

## A11 activate.py — exact-name prior
```diff
-            if self.symbol_key[i] in q_raw or (self.file_stem[i] and self.file_stem[i] in q_raw):
+            name = self.symbol_key[i]
+            if (len(name) >= 4 and name not in GENERIC_NAMES and name in q_raw) or (self.file_stem[i] and self.file_stem[i] in q_raw):
```
`GENERIC_NAMES = frozenset({"step", "body", "main", "run", "init", "test", "setup"})`

## A16 orchestrator.choose_mode
```diff
-    if any(term in text for term in hard_terms):
+    if re.search(r"\b(refactor\w*|migrat\w*|trace\w*|review|audit\w*|рефактор\w*|миграц\w*|трассир\w*|аудит\w*)\b|across files|cross-file", text):
         return "hard"
```
(`"traceback"` тоже попадёт под `trace\w*` — если не нужно, замените на `trace\b`.)
