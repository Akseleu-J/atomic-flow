"""Synthetic stdio LSP peer for host protocol acceptance; no real analyzer."""
import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
documents = {}


def send(value):
    data = json.dumps(value, ensure_ascii=False).encode()
    frame = b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data
    if mode == "fragmented":
        for offset in range(0, len(frame), 3):
            sys.stdout.buffer.write(frame[offset:offset + 3])
            sys.stdout.buffer.flush()
    else:
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()


def notify(uri, version):
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {
        "uri": uri, "version": version if mode != "stale-diagnostics" else version - 1,
        "diagnostics": [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                         "message": "synthetic diagnostic", "severity": 2}]}})


while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line == b"\r\n":
            break
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    body = sys.stdin.buffer.read(int(headers["content-length"]))
    request = json.loads(body)
    method, params = request.get("method"), request.get("params")
    if method is None:
        continue
    if method == "exit":
        sys.exit(0)
    if method == "textDocument/didOpen":
        doc = params["textDocument"]
        documents[doc["uri"]] = dict(doc)
        notify(doc["uri"], doc["version"])
        continue
    if method == "textDocument/didChange":
        doc = params["textDocument"]
        documents[doc["uri"]].update(version=doc["version"], text=params["contentChanges"][0]["text"])
        notify(doc["uri"], doc["version"])
        continue
    if "id" not in request:
        continue
    result = None
    if method == "initialize":
        if mode == "oversized":
            sys.stdout.buffer.write(b"Content-Length: 999999999\r\n\r\n")
            sys.stdout.buffer.flush()
            time.sleep(10)
            continue
        if mode == "bad-header":
            sys.stdout.buffer.write(b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}")
            sys.stdout.buffer.flush()
            continue
        if mode == "partial":
            sys.stdout.buffer.write(b"Content-Length: 20\r\n\r\n{")
            sys.stdout.buffer.flush()
            time.sleep(10)
            continue
        if mode == "environment":
            with open("child-env.json", "w", encoding="utf-8") as handle:
                json.dump({"secret": os.environ.get("GITHUB_TOKEN"), "pythonpath": os.environ.get("PYTHONPATH"),
                           "home": os.environ.get("HOME")}, handle)
        result = {"capabilities": {"positionEncoding": "utf-8" if mode == "utf8" else "utf-16",
                                   "textDocumentSync": 2 if mode == "incremental" else 1, "renameProvider": True}}
    elif method == "textDocument/definition" or method == "textDocument/references":
        if mode == "timeout":
            time.sleep(10)
            continue
        if mode == "server-error":
            send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -1, "message": "private-secret-value"}})
            continue
        if mode == "unsolicited":
            send({"jsonrpc": "2.0", "id": "server-apply", "method": "workspace/applyEdit",
                  "params": {"edit": {"changes": {params["textDocument"]["uri"]: []}}}})
        uri = params["textDocument"]["uri"]
        if mode == "outside":
            uri = "file:///outside/private.py"
        if mode == "private":
            uri = uri.rsplit("/", 1)[0] + "/.env"
        result = [{"uri": uri, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}]
    elif method == "textDocument/rename":
        edits = []
        for uri, doc in documents.items():
            value = {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                     "newText": params["newName"]}
            if mode in {"nonbmp", "half-surrogate"}:
                value["range"] = {"start": {"line": 0, "character": 5 if mode == "nonbmp" else 6},
                                  "end": {"line": 0, "character": 7}}
            changes = [value, value] if mode == "overlap" else [value]
            edits.append({"textDocument": {"uri": uri, "version": doc["version"] - (1 if mode == "stale-edit" else 0)},
                          "edits": changes})
        if mode == "resource":
            edits.append({"kind": "delete", "uri": params["textDocument"]["uri"]})
        if mode == "duplicate-target":
            edits.append(edits[0])
        if mode == "unversioned":
            result = {"changes": {item["textDocument"]["uri"]: item["edits"] for item in edits}}
        else:
            result = {"documentChanges": edits}
    send({"jsonrpc": "2.0", "id": request["id"], "result": result})
