/** Explicit process-only Kaggle Model Proxy adapter for the fixed reviewed queue model.
 * No credential discovery, environment reads, files, logging, retries or fallback.
 * expiresAt is a Unix UTC millisecond timestamp. Host admission owns URL/model
 * authorization and conservative reservations; this adapter never estimates cost.
 */
import {MAX_WIRE_BYTES, MODEL, validateRequest} from './p07_model_broker.mjs';

export const MAX_RESPONSE_BYTES = MAX_WIRE_BYTES;
class JsonNumber {
  constructor(source) { this.source = source; }
}
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.getPrototypeOf(value) === null;
const has = (value, key) => Object.hasOwn(value, key);

function failure(code, usageResult) {
  const error = new Error(code);
  if (usageResult) error.usage_result = usageResult;
  else error.request_outcome = 'unknown';
  return error;
}

/** Strict bounded JSON parser preserves monetary number lexemes before JS rounding.
 * Duplicate keys, excessive depth/nodes and trailing data are refused.
 */
function parseResponse(source) {
  let position = 0, nodes = 0;
  function whitespace() { while (/[\t\r\n ]/.test(source[position] || 'x')) position++; }
  function string() {
    const start = position++;
    while (position < source.length) {
      const c = source[position++];
      if (c === '"') return JSON.parse(source.slice(start, position));
      if (c === '\\') position++;
    }
    throw Error('shape');
  }
  function value(depth) {
    if (depth > 64 || ++nodes > 100000) throw Error('shape');
    whitespace();
    const c = source[position];
    if (c === '"') return string();
    if (c === '{') {
      position++; const object = Object.create(null); whitespace();
      if (source[position] === '}') { position++; return object; }
      while (true) {
        whitespace(); if (source[position] !== '"') throw Error('shape');
        const key = string(); if (has(object, key)) throw Error('shape');
        whitespace(); if (source[position++] !== ':') throw Error('shape');
        object[key] = value(depth + 1); whitespace();
        const next = source[position++]; if (next === '}') return object;
        if (next !== ',') throw Error('shape');
      }
    }
    if (c === '[') {
      position++; const array = []; whitespace();
      if (source[position] === ']') { position++; return array; }
      while (true) {
        array.push(value(depth + 1)); whitespace();
        const next = source[position++]; if (next === ']') return array;
        if (next !== ',') throw Error('shape');
      }
    }
    for (const [word, output] of [['true', true], ['false', false], ['null', null]]) {
      if (source.startsWith(word, position)) { position += word.length; return output; }
    }
    const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(position));
    if (!number) throw Error('shape');
    position += number[0].length; return new JsonNumber(number[0]);
  }
  const parsed = value(0); whitespace();
  if (position !== source.length || !plain(parsed)) throw Error('shape');
  return parsed;
}

function amount(value) {
  if (!(value instanceof JsonNumber) || value.source.length > 128) throw Error('usage');
  const match = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/.exec(value.source);
  const exponent = Number(match?.[4] || '0');
  if (!match || !Number.isSafeInteger(exponent) || Math.abs(exponent) > 128) throw Error('usage');
  let units = BigInt(match[2] + (match[3] || ''));
  let scale = (match[3] || '').length - exponent;
  if (match[1] && units !== 0n) throw Error('usage');
  if (scale < 0) { units *= 10n ** BigInt(-scale); scale = 0; }
  if (scale > 256 || units.toString().length > 256) throw Error('usage');
  return {units, scale};
}
function decimal({units, scale}) {
  while (scale && units % 10n === 0n) { units /= 10n; scale--; }
  const digits = units.toString();
  const output = scale === 0 ? digits : scale >= digits.length
    ? '0.' + '0'.repeat(scale - digits.length) + digits
    : digits.slice(0, digits.length - scale) + '.' + digits.slice(digits.length - scale);
  if (output.length > 64) throw Error('usage');
  return output;
}
function add(left, right) {
  const scale = Math.max(left.scale, right.scale);
  return {units: left.units * 10n ** BigInt(scale - left.scale)
    + right.units * 10n ** BigInt(scale - right.scale), scale};
}
function tokens(value, maximum) {
  if (!(value instanceof JsonNumber) || !/^(?:0|[1-9][0-9]*)$/.test(value.source)) throw Error('usage');
  const result = Number(value.source);
  if (!Number.isSafeInteger(result) || result < 0 || result > maximum) throw Error('usage');
  return result;
}
function usage(payload, req) {
  const data = payload.usage;
  if (!plain(data)) throw Error('usage');
  function consistentTokens(names, maximum) {
    const values = names.filter(name => has(data, name)).map(name => tokens(data[name], maximum));
    if (!values.length || values.some(value => value !== values[0])) throw Error('usage');
    return values[0];
  }
  function consistentComponents(name, nested) {
    const values = [nested, data].filter(object => has(object, name)).map(object => decimal(amount(object[name])));
    if (values.some(value => value !== values[0])) throw Error('usage');
    return values[0];
  }
  const incoming = consistentTokens(['prompt_tokens', 'input_tokens'], req.config.max_input_tokens);
  const outgoing = consistentTokens(['completion_tokens', 'output_tokens'], req.config.max_output_tokens);
  const totals = [];
  for (const name of ['cost_usd', 'total_cost_usd']) {
    if (has(data, name)) totals.push(decimal(amount(data[name])));
  }
  if (has(data, 'cost') && !plain(data.cost)) totals.push(decimal(amount(data.cost)));
  const nested = plain(data.cost) ? data.cost : Object.create(null);
  const input = consistentComponents('input_tokens_cost_nanodollars', nested);
  const output = consistentComponents('output_tokens_cost_nanodollars', nested);
  if ((input === undefined) !== (output === undefined)) throw Error('usage');
  if (input !== undefined) {
    const cost = add(amount(new JsonNumber(input)), amount(new JsonNumber(output)));
    cost.scale += 9;
    totals.push(decimal(cost));
  }
  if (!totals.length || totals.some(value => value !== totals[0])) throw Error('usage');
  return {tokens_in: incoming, tokens_out: outgoing, cost_usd: totals[0]};
}
async function boundedBody(response) {
  const length = response.headers?.get?.('content-length');
  if (length !== null && length !== undefined
      && (!/^[0-9]+$/.test(length) || Number(length) > MAX_RESPONSE_BYTES)) throw Error('response');
  if (!response.body || typeof response.body.getReader !== 'function') throw Error('response');
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      // Fetch readers may originate in another VM realm (desktop Node REPL).
      // instanceof rejects valid byte views there; retain an actual byte-view
      // check rather than admitting arbitrary array-like values.
      if (!ArrayBuffer.isView(item.value) || item.value.BYTES_PER_ELEMENT !== 1
          || typeof item.value.length !== 'number' || item.value.byteLength !== item.value.length) throw Error('response');
      size += item.value.byteLength;
      if (size > MAX_RESPONSE_BYTES) { await reader.cancel(); throw Error('response'); }
      chunks.push(item.value);
    }
  } finally { reader.releaseLock(); }
  const buffer = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder('utf-8', {fatal: true}).decode(buffer);
}

export function createKaggleProxy({baseUrl, apiKey, expiresAt, fetchImpl = fetch, now = () => Date.now(), timeoutMs = 120000}) {
  let endpoint;
  try {
    if (typeof baseUrl !== 'string' || !baseUrl || baseUrl.length > 2048 || /[\s\\]/.test(baseUrl)) throw Error();
    const url = new URL(baseUrl);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash
        || (url.port && url.port !== '443') || /%|\/\.\.?\//.test(baseUrl)) throw Error();
    let pathname = url.pathname.replace(/\/+$/, '');
    if (pathname.includes('//') || /(?:^|\/)v1(?:\/|$)/.test(pathname) || pathname.endsWith('/chat/completions') || pathname.endsWith('/v1')) throw Error();
    if (!pathname.endsWith('/openapi')) pathname += '/openapi';
    endpoint = url.origin + pathname + '/chat/completions';
    if (typeof apiKey !== 'string' || !/^[\x21-\x7e]{1,8192}$/.test(apiKey)
        || !Number.isSafeInteger(expiresAt) || expiresAt <= 0 || typeof fetchImpl !== 'function'
        || typeof now !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000) throw Error();
    const current = now();
    if (!Number.isSafeInteger(current) || current <= 0 || current >= expiresAt) throw Error();
  } catch { throw new Error('kaggle_proxy_host_configuration_refused'); }
  const sent = new Set();
  return {
    async complete(req) {
      try { validateRequest(req); } catch { throw new Error('kaggle_proxy_request_refused'); }
      let started;
      try { started = now(); } catch { throw new Error('kaggle_proxy_request_refused'); }
      if (!Number.isSafeInteger(started) || started <= 0 || started >= expiresAt || req.config.model !== MODEL
          || req.config.max_retries !== 0 || sent.has(req.call_id)) throw new Error('kaggle_proxy_request_refused');
      const body = {model: MODEL, messages: req.messages, max_tokens: req.config.max_output_tokens};
      if (req.config.temperature !== null) body.temperature = Number(req.config.temperature);
      // Provider HTTP bodies permit the reviewed fractional temperature; the
      // data-queue canonical hash protocol intentionally admits integer numbers only.
      const encoded = JSON.stringify(body);
      if (Buffer.byteLength(encoded) > MAX_WIRE_BYTES) throw new Error('kaggle_proxy_request_refused');
      const controller = new AbortController();
      let timer;
      sent.add(req.call_id);
      const outcome = (async () => {
        let response, payload;
        try {
          response = await fetchImpl(endpoint, {method: 'POST', headers: {'Authorization': 'Bearer ' + apiKey,
            'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: encoded, redirect: 'error', signal: controller.signal});
          if (!response || response.status !== 200 || response.redirected) throw Error('http');
          payload = parseResponse(await boundedBody(response));
        } catch { throw failure('kaggle_proxy_transport_or_response_unknown'); }
        if (payload.model !== MODEL) throw failure('kaggle_proxy_response_model_unknown');
        let actual;
        try { actual = usage(payload, req); } catch { throw failure('kaggle_proxy_usage_unknown'); }
        let ended;
        try { ended = now(); } catch { ended = started; }
        const result = {content: '', ...actual, latency_ms: Number.isSafeInteger(ended) && ended >= started
          ? Math.min(86400000, ended - started) : 0, truncated: false, cost_source: 'provider',
          resolved_backend: 'kaggle_model_proxy', resolved_model: MODEL, attempt_id: req.call_id};
        const choices = payload.choices;
        const choice = Array.isArray(choices) && choices.length === 1 ? choices[0] : null;
        const message = plain(choice) && plain(choice.message) ? choice.message : null;
        if (has(payload, 'error') || !message || typeof message.content !== 'string'
            || Buffer.from(message.content, 'utf8').toString('utf8') !== message.content
            || Buffer.byteLength(message.content, 'utf8') > MAX_WIRE_BYTES - 2048
            || (has(message, 'tool_calls') && (!Array.isArray(message.tool_calls) || message.tool_calls.length))
            || !['stop', 'length'].includes(choice.finish_reason)) {
          throw failure('kaggle_proxy_response_shape', result);
        }
        result.content = message.content;
        result.truncated = choice.finish_reason === 'length';
        return result;
      })();
      try {
        const timeout = new Promise((resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(failure('kaggle_proxy_timeout_unknown')); }, timeoutMs);
        });
        return await Promise.race([outcome, timeout]);
      } finally { clearTimeout(timer); }
    },
  };
}
