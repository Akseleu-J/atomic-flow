# Role prompts

Prompts are plain UTF-8 Markdown or text files named after their role:

```text
.atomic_flow/prompts/answerer.md
~/.atomic_flow/prompts/critic.md
```

Request overrides use `--role-prompt ROLE=TEXT` or `--role-prompt ROLE=@FILE`. Available roles are `parser`, `refiner`, `answerer`, `critic`, `expander`, and `reparser`. The prompts must instruct the model to return the JSON shape described in `atomic_flow.llm.schemas`.

`atomic ask ... --edit-prompts` creates project-level files for the selected mode, opens them in `$EDITOR` (or Notepad on Windows), and exits. Rerun the query after editing.
