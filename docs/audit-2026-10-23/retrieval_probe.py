import json,sys,time
from pathlib import Path
sys.path.insert(0,'src')
from atomic_flow.benchmarking import benchmark_root, benchmark_cases
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode
from atomic_flow.activation.activate import ActivationIndex
start=time.perf_counter()
tree=build_tree(benchmark_root()/'fixtures')
index=ActivationIndex([TreeNode.model_validate(n) for n in tree['nodes']])
rows=[]
for c in benchmark_cases():
    expected=c.get('expected_nodes',[])
    if c.get('skip_retrieval') or not expected:continue
    result=index.search(c['query'],limit=10)
    ids=[n.node_id for n,_ in result.ranked]
    ranks=[ids.index(e)+1 if e in ids else None for e in expected]
    rows.append({'case_id':c['id'],'recall_at_5':sum(bool(r and r<=5) for r in ranks)/len(expected),'hit_all_at_5':all(r and r<=5 for r in ranks),'mrr':1/min(r for r in ranks if r) if any(ranks) else 0})
report={'kind':'current-fixture read-only retrieval check, not edit quality or scale acceptance','model_calls':0,'fixture_files':tree['files'],'nodes':len(tree['nodes']),'elapsed_seconds':round(time.perf_counter()-start,4),'cases':len(rows),'mean_recall_at_5':sum(r['recall_at_5'] for r in rows)/len(rows),'hit_all_at_5':sum(r['hit_all_at_5'] for r in rows)/len(rows),'mrr':sum(r['mrr'] for r in rows)/len(rows),'results':rows}
print(json.dumps(report,indent=2))
