// Run: node --test tests/upload-proxy.test.cjs (no additional dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { NextRequest } = require('next/server');
const filename = process.env.PROXY_ROUTE_FILE || path.resolve(__dirname, '../app/api/[...path]/route.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const loaded = new Module(filename); loaded.paths = Module._nodeModulePaths(path.resolve(__dirname, '..')); loaded._compile(compiled, filename);
const forward = loaded.exports.POST;
const context = route => ({ params: Promise.resolve({ path: [route] }) });
function request(body, headers = {}) {
  return new NextRequest('http://localhost/api/uploads', { method: 'POST', duplex: 'half', body,
    headers: { 'Content-Type': 'multipart/form-data; boundary=original', Authorization: 'Bearer test-only', ...headers } });
}
async function withFetch(mock, fn) {
  const original = global.fetch, endpoint = process.env.BACKEND_URL;
  global.fetch = mock; process.env.BACKEND_URL = 'http://backend.test:8000';
  try { await fn(); } finally { global.fetch = original; if (endpoint === undefined) delete process.env.BACKEND_URL; else process.env.BACKEND_URL = endpoint; }
}

test('body stream failure returns JSON 400 before upstream fetch', () => withFetch(() => assert.fail('upstream must not be called'), async () => {
  const body = new ReadableStream({ start(c) { c.error(new Error('simulated body read failure')); } });
  const result = await forward(request(body), context('uploads'));
  assert.equal(result.status, 400); assert.match(result.headers.get('content-type'), /application\/json/);
  assert.equal((await result.json()).detail, 'Could not read request body');
}));

test('multipart bytes and original boundary/cookie/token survive ArrayBuffer forwarding', () => {
  const bytes = Buffer.from('--original\r\n\x00\xff\r\n--original--\r\n', 'latin1');
  return withFetch(async (url, options) => {
    assert.equal(options.headers['Content-Type'], 'multipart/form-data; boundary=original');
    assert.equal(options.headers.Cookie, 'autolog_admin=test-session');
    assert.equal(options.headers.Authorization, 'Bearer test-only');
    assert.deepEqual(Buffer.from(options.body), bytes);
    assert.equal(options.headers['Content-Length'], undefined); // fetch computes it; never copy stale inbound length.
    return Response.json({ url: 'https://img.example.com/uploads/test.png' }, { status: 201 });
  }, async () => { assert.equal((await forward(request(bytes, { Cookie: 'other=unused; autolog_admin=test-session' }), context('uploads'))).status, 201); });
});

test('chunked oversized body returns JSON 413 without reaching backend', () => withFetch(() => assert.fail('upstream must not be called'), async () => {
  const body = new ReadableStream({ start(c) { for (let i=0;i<11;i++) c.enqueue(new Uint8Array(1024*1024)); c.close(); } });
  const result = await forward(request(body), context('uploads'));
  assert.equal(result.status, 413); assert.equal((await result.json()).detail, 'Request is too large');
}));

test('oversized declared length is rejected before reading', () => withFetch(() => assert.fail('upstream must not be called'), async () => {
  assert.equal((await forward(request('small', { 'Content-Length': '20000000' }), context('uploads'))).status, 413);
}));

test('upstream HTML 502 becomes JSON, never raw HTML', () => withFetch(async () => new Response('<html>upstream failed</html>', { status:502 }), async () => {
  const result = await forward(request('small'), context('uploads'));
  assert.equal(result.status,502); const body=await result.json(); assert.equal(body.detail,'Backend returned a non-JSON response'); assert.ok(body.request_id);
}));

test('upstream connection reset returns JSON 502', () => withFetch(async () => { throw new TypeError('simulated reset'); }, async () => {
  const result = await forward(request('small'), context('uploads'));
  assert.equal(result.status,502); assert.equal((await result.json()).detail,'Backend is unavailable');
}));

test('JSON forwarding and DELETE 204 remain unchanged', () => withFetch(async (url, options) => {
  assert.equal(options.headers['Content-Type'],'application/json');
  assert.equal(Buffer.from(options.body).toString(),'{"title":"saved"}');
  return new Response(null,{status:204});
}, async () => {
  const req=new NextRequest('http://localhost/api/articles',{method:'DELETE',headers:{'Content-Type':'application/json'},body:'{"title":"saved"}'});
  const result=await forward(req,context('articles'));assert.equal(result.status,204);assert.equal(await result.text(),'');
}));

test('cookie upload with another Origin remains denied', () => withFetch(() => assert.fail('upstream must not be called'),async () => {
  const req=request('small',{Authorization:'',Cookie:'autolog_admin=test-session',Origin:'https://evil.example'});
  assert.equal((await forward(req,context('uploads'))).status,403);
}));
