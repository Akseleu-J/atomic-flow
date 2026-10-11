# Troubleshooting

- **No project tree:** run `atomic init --project` and `atomic tree build` from the repository root.
- **No backend configured:** set `ATOMIC_FLOW_API_KEY` and `ATOMIC_FLOW_BASE_URL`, or edit `~/.atomic_flow/config.yaml`.
- **Backend connection error:** check the base URL. It should normally end in `/v1`, unless it already ends in `/chat/completions`. Use `atomic doctor --check-backend` to send a live request.
- **Invalid role JSON:** confirm that the configured model supports JSON output and that custom prompts ask it to match the supplied schema.
- **Empty or irrelevant context:** use `atomic tree explain` to inspect indexed symbols, narrow with `--file`, and increase `--budget` if context was truncated.
- **Hard mode confirmation:** provide `--yes` on the CLI or set `acknowledge_default_prompts: true` in an API request.
