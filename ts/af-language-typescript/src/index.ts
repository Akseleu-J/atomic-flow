/** Fixed TS compiler analysis over admitted in-memory bytes. No project IO/config/plugins/execution. */
import ts from "typescript";
import { createHash } from "node:crypto";

export const SCHEMA = "source-analysis/1";
export const EXTRACTOR = "typescript-compiler/1";
export const MAX_FILE_BYTES = 1_000_000;
export const MAX_REQUEST_BYTES = 12_000_000;
const HASH = /^[a-f0-9]{64}$/;
type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
interface InputFile { path: string; source: string; sha256: string }
interface Input { root_id: string; source_revision: string; files: InputFile[] }
export interface Analysis { schema: string; root_id: string; source_revision: string;
  sources: Json[]; nodes: Json[]; edges: Json[]; imports: Json[]; omissions: Json[]; verification: "not_assessed" }

function object(value: unknown, keys: string[]): asserts value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join("\0") !== [...keys].sort().join("\0")) throw Error("exact object fields required");
}
export function validPath(path: string): boolean {
  return path.length > 0 && path.length <= 512 && !/[\\:\0]/.test(path)
    && path.split("/").every(p => p !== "" && p !== "." && p !== "..");
}
const digest = (source: string): string => createHash("sha256").update(source).digest("hex");
function input(value: unknown): Input {
  if (new TextEncoder().encode(JSON.stringify(value)).length > MAX_REQUEST_BYTES) throw Error("request byte ceiling");
  object(value, ["root_id", "source_revision", "files"]);
  if (typeof value.root_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.root_id)
      || typeof value.source_revision !== "string" || !HASH.test(value.source_revision)
      || !Array.isArray(value.files) || !value.files.length || value.files.length > 256) throw Error("bounded source context required");
  const seen = new Set<string>(); let total = 0;
  for (const file of value.files) {
    object(file, ["path", "source", "sha256"]);
    if (typeof file.path !== "string" || !validPath(file.path) || !/\.(ts|tsx|mts|cts)$/.test(file.path)
        || typeof file.source !== "string" || file.source.includes("\0")
        || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(file.source)
        || typeof file.sha256 !== "string" || !HASH.test(file.sha256) || digest(file.source) !== file.sha256
        || seen.has(file.path)) throw Error("source identity/bytes differ");
    const size = new TextEncoder().encode(file.source).length;
    total += size; if (size > MAX_FILE_BYTES || total > 8_000_000) throw Error("source byte ceiling");
    seen.add(file.path);
  }
  return value as unknown as Input;
}
const fileId = (path: string): string => `file:${path}`;
const edgeId = (src: string, kind: string, dst: string): string => `${src}-${kind}->${dst}`;

export function analyze(value: unknown): Analysis {
  const request = input(value);
  if (ts.version !== "5.8.3") throw Error("admitted TypeScript 5.8.3 required");
  const files = new Map(request.files.map(f => [f.path, f]));
  const options: ts.CompilerOptions = { noLib: true, noResolve: false, target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext, strict: true, noEmit: true, types: [], allowJs: false,
    jsx: ts.JsxEmit.Preserve };
  const sourceFiles = new Map(request.files.map(f => [f.path, ts.createSourceFile(f.path, f.source,
    options.target!, true, f.path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS)]));
  function resolve(specifier: string, containing: string): string | undefined {
    if (!specifier.startsWith(".")) return undefined;
    const parts = containing.split("/"); parts.pop();
    for (const part of specifier.split("/")) {
      if (part === "." || !part) continue;
      if (part === "..") { if (!parts.length) return undefined; parts.pop(); } else parts.push(part);
    }
    const base = parts.join("/");
    const candidates = [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`];
    const found = candidates.filter(p => files.has(p));
    return found.length === 1 ? found[0] : undefined;
  }
  const host: ts.CompilerHost = {
    getSourceFile: name => sourceFiles.get(name), getDefaultLibFileName: () => "",
    writeFile: () => { throw Error("emit forbidden"); }, getCurrentDirectory: () => "",
    getDirectories: () => [], fileExists: name => files.has(name), readFile: name => files.get(name)?.source,
    getCanonicalFileName: name => name, useCaseSensitiveFileNames: () => true, getNewLine: () => "\n",
    resolveModuleNames: (names, containing) => names.map(name => {
      const path = resolve(name, containing);
      return path ? { resolvedFileName: path, extension: path.endsWith(".tsx") ? ts.Extension.Tsx : ts.Extension.Ts } : undefined;
    }),
  };
  const program = ts.createProgram([...files.keys()], options, host);
  const checker = program.getTypeChecker();
  const nodes: Json[] = [], edges: Json[] = [], imports: Json[] = [], omissions: Json[] = [];
  const declarations = new Map<ts.Declaration, string>();
  let visited = 0;
  const pending: Array<{ source: string; call: ts.CallExpression; path: string; hash: string }> = [];
  for (const [path, sf] of sourceFiles) {
    const source = files.get(path)!; const owner = fileId(path);
    const diagnostics = program.getSyntacticDiagnostics(sf);
    const facts: { [key: string]: Json } = { extractor: EXTRACTOR, language: "typescript",
      parse_ok: diagnostics.length === 0, imports: [], raw_calls: [], semantic_status: "partial",
      diagnostics: diagnostics.slice(0, 100).map(d => ({ code: d.code, start: d.start ?? 0,
        message: ts.flattenDiagnosticMessageText(d.messageText, "\n") })),
      capabilities: ["syntax", "named_declarations", "relative_imports_in_admitted_files", "direct_symbol_calls"],
      omissions: ["project_tsconfig", "default_libraries", "packages", "path_aliases", "overload_dispatch", "dynamic_import_resolution", "type_correctness"] };
    nodes.push({ id: owner, kind: "file", path, source: { digest: source.sha256, algorithm: "sha256" },
      facts, fact_status: Object.fromEntries(Object.keys(facts).map(k => [k, "fresh"])), facts_class: "extracted" });
    omissions.push({ path, reason: "fixed_in_memory_program_without_project_config_libraries_or_execution" });
    if (diagnostics.length) { omissions.push({ path, reason: "syntax_errors_no_symbols_extracted" }); continue; }
    const seen = new Map<string, number>();
    function visit(node: ts.Node, parent: string, parentId: string): void {
      if (++visited > 100_000 || nodes.length > 50_000 || edges.length > 100_000) throw Error("analysis structural ceiling");
      let qualified = parent, nodeId = parentId;
      const name = (ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node)
        || ts.isEnumDeclaration(node) || ts.isMethodDeclaration(node)) ? node.name : undefined;
      if (name && ts.isIdentifier(name) && /^[A-Za-z_][A-Za-z0-9_]*$/.test(name.text)) {
        qualified = parent ? `${parent}.${name.text}` : name.text;
        const count = (seen.get(qualified) ?? 0) + 1; seen.set(qualified, count);
        nodeId = `sym:${path}::${qualified}${count === 1 ? "" : `#${count}`}`;
        const kind = ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) ? "function" : "class";
        const first = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
        const last = sf.getLineAndCharacterOfPosition(Math.max(node.getStart(sf), node.end - 1)).line + 1;
        nodes.push({ id: nodeId, kind, path, symbol: qualified, source: { digest: source.sha256, algorithm: "sha256" },
          start_line: first, end_line: last, facts: { extractor: EXTRACTOR, syntax_kind: ts.SyntaxKind[node.kind]!, semantic_status: "partial" },
          fact_status: { extractor: "fresh", syntax_kind: "fresh", semantic_status: "fresh" }, facts_class: "extracted" });
        declarations.set(node as ts.Declaration, nodeId);
        edges.push({ id: edgeId(parentId, "contains", nodeId), kind: "contains", src: parentId, dst: nodeId,
          status: "active", facts: { extractor: EXTRACTOR }, fact_status: { extractor: "fresh" }, facts_class: "extracted" });
      } else if (name) {
        omissions.push({ path, reason: "non_identifier_or_non_ascii_symbol_not_representable_in_graph_v1" });
      }
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        const specifier = node.moduleSpecifier.text, resolved = resolve(specifier, path);
        const record: Json = { specifier, resolved_path: resolved ?? null, resolution: resolved ? "admitted_relative_file" : "not_assessed",
          start_line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
          end_line: sf.getLineAndCharacterOfPosition(node.end - 1).line + 1 };
        imports.push({ path, source_sha256: source.sha256, record });
        if (resolved) edges.push({ id: edgeId(owner, "imports", fileId(resolved)), kind: "imports", src: owner, dst: fileId(resolved),
          status: "active", facts: { extractor: EXTRACTOR, resolution: "admitted_relative_file" },
          fact_status: { extractor: "fresh", resolution: "fresh" }, facts_class: "inferred" });
      }
      if (ts.isCallExpression(node)) pending.push({ source: nodeId, call: node, path, hash: source.sha256 });
      ts.forEachChild(node, child => visit(child, qualified, nodeId));
    }
    visit(sf, "", owner);
  }
  for (const item of pending) {
    let symbol = checker.getSymbolAtLocation(item.call.expression);
    if (symbol && (symbol.flags & ts.SymbolFlags.Alias)) symbol = checker.getAliasedSymbol(symbol);
    const candidates = symbol?.declarations?.map(d => declarations.get(d)).filter((s): s is string => !!s) ?? [];
    if (candidates.length === 1 && candidates[0] !== item.source) edges.push({ id: edgeId(item.source, "calls", candidates[0]!), kind: "calls",
      src: item.source, dst: candidates[0]!, status: "active", facts: { extractor: EXTRACTOR, resolution: "compiler_direct_symbol" },
      fact_status: { extractor: "fresh", resolution: "fresh" }, facts_class: "inferred" });
    else omissions.push({ path: item.path, reason: candidates[0] === item.source
      ? "recursive_self_edge_not_representable_in_graph_v1" : "call_target_unresolved_or_ambiguous", source_sha256: item.hash });
  }
  // Graph edge identity is unique even if several syntax sites express the same dependency.
  const uniqueEdges = [...new Map(edges.map(edge => [(edge as Record<string, Json>).id as string, edge])).values()];
  return { schema: SCHEMA, root_id: request.root_id, source_revision: request.source_revision,
    sources: request.files.map(f => ({ path: f.path, sha256: f.sha256 })), nodes, edges: uniqueEdges,
    imports, omissions, verification: "not_assessed" };
}
