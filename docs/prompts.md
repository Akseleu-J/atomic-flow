# Role prompts

Plain UTF-8 files named after their role: `.atomic_flow/prompts/answerer.md`, `~/.atomic_flow/prompts/critic.md`.
Override per request with `--role-prompt ROLE=TEXT` or `ROLE=@FILE`. Roles: `parser`, `refiner`, `answerer`, `critic`, `expander`, `reparser`.

The pipeline prepends a fixed safety preamble (repository text is data, not instructions) and appends a short example of the
JSON shape, so custom prompts only describe *behaviour*. Prompt lookup: request > project > user > builtin.
Nodes are referred to by short ids (`n12`) exactly as shown in the context.
