"""No-inference preflight of the exact workspace used by the paid source pilot."""
import argparse
import json
import tempfile
from pathlib import Path

from evaluation_p07_source_pilot import READ_PATHS, TASK_ID, prepare_source_workspace
from evaluation_stage4 import digest, load_manifest

from atomic_flow.sdk import inspect_graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mirror', required=True)
    args = parser.parse_args()
    task = next(t for t in load_manifest('benchmarks/stage4/corpus.json')['tasks'] if t['instance_id'] == TASK_ID)
    with tempfile.TemporaryDirectory(prefix='p07-preflight-') as directory:
        work = Path(directory)
        prepare_source_workspace(args.mirror, task['base_commit'], work)
        inspection = inspect_graph(work, 'inspect', byte_cap=1_000_000)
        print(json.dumps({'status': 'source_workspace_ready', 'model_calls': 0,
                          'base_commit': task['base_commit'], 'inspection': inspection,
                          'files': {path: digest((work / path).read_bytes()) for path in sorted(READ_PATHS)}}))


if __name__ == '__main__':
    main()
