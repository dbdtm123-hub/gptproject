const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(name, dependencies = {}) {
  const filename = path.resolve(__dirname, '../lib', name + '.ts');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = new Module(filename);
  loaded.require = name => dependencies[name] || require(name);
  loaded._compile(compiled, filename);
  return loaded.exports;
}
const images = load('image-upload');
const { uploadAuthorizedImage } = load('autolog-image-transport', { './image-upload': images });
const transport = load('autolog-image-transport', { './image-upload': images });
const { createUploadImageTool } = load('sites-upload-image', { './autolog-image-transport': transport });
const key = 'uploads/2026/10/00000000000040008000000000000000.png';
const result = { key, url: 'https://img.example.com/' + key };
const connection = { backendOrigin: 'https://autolog.example.com', publicImageBase: 'https://img.example.com', token: 'test-token' };
const file = new File(['test-bytes'], 'image.png', { type: 'image/png' });

test('successful multipart adapter preserves bytes and returns escaped Markdown', async () => {
  const uploaded = await uploadAuthorizedImage(file, '구성 [도식]\n결과', connection, async (url, options) => {
    assert.equal(url.href, 'https://autolog.example.com/api/uploads');
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit');
    const sent = options.body.get('file');
    assert.equal(sent.type, 'image/png'); assert.equal(await sent.text(), 'test-bytes');
    return Response.json(result, { status: 201 });
  });
  assert.equal(uploaded.markdown, `![구성 \\[도식\\] 결과](${result.url})`);
});

test('local validation prevents unauthenticated, empty, oversized and SVG requests', async () => {
  const never = () => assert.fail('must not send');
  for (const [blob, alt, config, status] of [
    [file, '도식', { ...connection, token: '' }, 401],
    [file, ' ', connection, 422],
    [new Blob([], { type: 'image/png' }), '도식', connection, 422],
    [new Blob([new Uint8Array(10 * 1024 * 1024 + 1)], { type: 'image/png' }), '도식', connection, 413],
    [new Blob(['<svg/>'], { type: 'image/svg+xml' }), '도식', connection, 415],
  ]) await assert.rejects(uploadAuthorizedImage(blob, alt, config, never), error => error.status === status);
});

test('upstream errors never expose body secrets and are never retried', async () => {
  for (const status of [401, 403, 413, 415, 422, 502, 503]) {
    let calls = 0;
    await assert.rejects(uploadAuthorizedImage(file, '도식', connection, async () => {
      calls++; return new Response('secret-token-in-upstream-body', { status });
    }), error => error.status === status && !error.message.includes('secret-token'));
    assert.equal(calls, 1);
  }
});

test('wrong keys, origins, non-JSON, huge replies and network errors never yield Markdown', async () => {
  for (const reply of [
    () => Response.json({ ...result, url: 'https://evil.example/' + key }, { status: 201 }),
    () => Response.json({ key: '../bad.png', url: result.url }, { status: 201 }),
    () => new Response('<html>secret</html>', { status: 201 }),
    () => new Response('x'.repeat(16385), { status: 201, headers: { 'Content-Type': 'application/json' } }),
    () => { throw new Error('signed-url-secret'); },
  ]) await assert.rejects(uploadAuthorizedImage(file, '도식', connection, reply), e => e.status === 502 && !e.message.includes('secret'));
});

test('identical file bytes, including concurrent selections, reuse a successful upload', async () => {
  const cache = new images.ImageUploadCache(); let calls = 0;
  const send = async () => { calls++; return result; };
  await Promise.all([cache.upload(file, send), cache.upload(new File(['test-bytes'], 'renamed.png'), send)]);
  await cache.upload(file, send);
  assert.equal(calls, 1);
});

test('failed uploads are not cached, and a different editor has its own cache', async () => {
  const cache = new images.ImageUploadCache(); let calls = 0;
  await assert.rejects(cache.upload(file, async () => { throw new Error('storage unavailable'); }));
  await cache.upload(file, async () => { calls++; return result; });
  await new images.ImageUploadCache().upload(file, async () => { calls++; return result; });
  assert.equal(calls, 2);
});

const ref = { download_url: 'https://verified-file-source.example/image?signed=private', file_id: 'file-test' };
const input = { image: ref, alt: '구성도' };
const request = new Request('https://site.example/mcp');
// Deliberately only a signature: mock backend. Actual decode remains a backend test.
const png = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 1]);
function dependencies(overrides = {}) {
  return {
    requireConnection: async () => connection,
    openAuthorizedFile: async () => new Response(png),
    backendFetch: async () => Response.json(result, { status: 201 }),
    ...overrides,
  };
}

test('Sites FileRef without optional hints flows through authorized reader and existing multipart API', async () => {
  const handler = createUploadImageTool(dependencies({
    openAuthorizedFile: async (req, fileRef, signal) => {
      assert.equal(req, request); assert.deepEqual(fileRef, ref); assert.ok(signal instanceof AbortSignal);
      assert.equal(fileRef.token, undefined); return new Response(png);
    },
    backendFetch: async (url, options) => {
      assert.equal(url.href, 'https://autolog.example.com/api/uploads');
      const blob = options.body.get('file');
      assert.equal(blob.type, 'image/png'); assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), png);
      return Response.json(result, { status: 201 });
    },
  }));
  const response = await handler(request, input);
  assert.equal(response.isError, false); assert.equal(response.structuredContent.markdown, `![구성도](${result.url})`);
  assert.ok(!JSON.stringify(response).includes('signed=private'));
});

test('Sites tool cannot be constructed without the actual host integrations', () => {
  assert.throws(() => createUploadImageTool({ requireConnection: async () => connection }), /Do not register/);
});

test('Sites tool authenticates before file reading and rejects path/text/extra-secret inputs', async () => {
  const never = async () => assert.fail('must not open or upload');
  const denied = createUploadImageTool(dependencies({ requireConnection: async () => ({ ...connection, token: '' }), openAuthorizedFile: never }));
  assert.equal(JSON.parse((await denied(request, input)).content[0].text).error.status, 401);
  const handler = createUploadImageTool(dependencies({ openAuthorizedFile: never, backendFetch: never }));
  for (const args of [
    { image: 'sandbox:/image.png', alt: '도식' },
    { ...input, token: 'not-an-input' },
    { image: { ...ref, download_url: 'http://127.0.0.1/image' }, alt: '도식' },
    { image: { ...ref, download_url: 'file:///image.png' }, alt: '도식' },
    { image: { ...ref, token: 'not-an-input' }, alt: '도식' },
    { ...input, alt: ' ' },
  ]) assert.equal(JSON.parse((await handler(request, args)).content[0].text).error.status, 422);
});

test('Sites reader bounds declared and chunked file sizes before reaching AutoLog', async () => {
  for (const response of [
    new Response(png, { headers: { 'Content-Length': String(10 * 1024 * 1024 + 1) } }),
    new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(10 * 1024 * 1024)); c.enqueue(new Uint8Array(1)); c.close(); } })),
  ]) {
    const handler = createUploadImageTool(dependencies({ openAuthorizedFile: async () => response, backendFetch: async () => assert.fail('must not upload') }));
    assert.equal(JSON.parse((await handler(request, input)).content[0].text).error.status, 413);
  }
});

test('Sites source failures and SVG return isError without URLs, secrets or success output', async () => {
  for (const reader of [async () => { throw new Error('signed-private-source-secret'); }, async () => new Response('<svg/>')]) {
    const handler = createUploadImageTool(dependencies({ openAuthorizedFile: reader, backendFetch: async () => assert.fail('must not upload') }));
    const response = await handler(request, input);
    assert.equal(response.isError, true); assert.equal(response.structuredContent, undefined);
    assert.ok(!JSON.stringify(response).includes('private-source-secret'));
  }
});

test('Sites upload concurrency limit releases its slot after completion', async () => {
  let release, started;
  const waiting = new Promise(resolve => { started = resolve; });
  const handler = createUploadImageTool(dependencies({ openAuthorizedFile: async () => {
    started(); return new Promise(resolve => { release = () => resolve(new Response(png)); });
  } }));
  const first = handler(request, input); await waiting;
  const busy = await handler(request, input);
  assert.equal(JSON.parse(busy.content[0].text).error.status, 429);
  release(); assert.equal((await first).isError, false);
  const third = handler(request, input); await Promise.resolve(); await Promise.resolve();
  release(); assert.equal((await third).isError, false);
});
