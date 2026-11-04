// Runs from a temporary installed package outside the source checkout.
import assert from 'node:assert/strict';
import {AfClient} from '@atomic-flow/af-client';
const client = new AfClient({baseUrl: process.env.AF_CLIENT_BASE, token: process.env.STUDIO_TEST_TOKEN});
await client.health();
const capabilities = await client.capabilities();
assert.equal(capabilities.requirement_checker, 'unavailable');
assert.equal(capabilities.verification_report_authorizes_execution, false);
const created = await client.createSession('sdk-consumer', 'pilot');
const session = await client.readSession(created.session_id);
assert.equal(session.session_id, created.session_id);
assert.deepEqual(session.unresolved_call_ids, []);
console.log(JSON.stringify({consumer: 'installed_copy_outside_checkout', capability: 'health_capabilities_create_read', ok: true, provider_calls: 0}));
