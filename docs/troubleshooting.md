# Troubleshooting

- **No project tree / outdated format:** `atomic init --project` then `atomic tree build`.
- **Index looks stale:** `atomic tree status` lists added/removed/modified files; `atomic tree refresh` fixes it (`ask` does this automatically).
- **Project config ignored:** a cloned repo cannot set backends. Review `.atomic_flow/config.yaml`, then `atomic trust`.
- **No backend configured:** set `ATOMIC_FLOW_API_KEY` (+`ATOMIC_FLOW_BASE_URL`, `ATOMIC_FLOW_MODEL`) or `ANTHROPIC_API_KEY`, or edit `~/.atomic_flow/config.yaml`.
- **Backend connection error:** base URL normally ends in `/v1`; `atomic doctor --check-backend` sends a live request.
- **Invalid role JSON:** each call gets one automatic repair round; persistent failures leave `*_raw.txt` in the run directory.
- **Empty/irrelevant context:** `atomic ask ... --dry-run --explain`, narrow with `--file`, raise `--budget`. Non-English queries only retrieve through the Parser's English `focus_terms`.
- **Budget exceeded:** `budget.daily_limit_usd` (0 disables). Calls without reported cost are not counted unless prices are configured.
- **Hard mode confirmation:** `--yes` on the CLI or `acknowledge_default_prompts: true` in an API request (the `atomic-hard` chat model counts as acknowledgment).
