"""Private CPU runtime admission probe; never sends a model prompt."""
import os
import platform
from pathlib import Path

import kaggle_benchmarks as kbench


@kbench.task(name="atomic-p07-source-preflight-20261026", description="CPU and process-only proxy admission for a bounded source-only development pilot; zero prompts.")
def p07_source_preflight(llm) -> dict:
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return {"status": "resource_preflight_only", "python": platform.python_version(),
            "available_ram_kib": memory["MemAvailable"].strip(), "provider_calls": 0,
            "proxy_environment_present": {name: bool(os.getenv(name)) for name in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY")},
            "target_setup_or_tests_executed": False, "isolation_claim": "none; source-only generation runtime"}


p07_source_preflight.run(kbench.llm)
