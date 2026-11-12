const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const { NextRequest } = require('next/server');
function load(relative) {
  const filename = path.resolve(__dirname, relative);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const m = new Module(filename); m.paths = Module._nodeModulePaths(path.resolve(__dirname, '..')); m._compile(code, filename); return m.exports;
}
const { compareMetrics } = load('../lib/reports.ts');
test('metric comparison preserves missing observations and never compares incompatible units', () => {
  const rows = compareMetrics([{name:'writes', value: 10, unit:'MB/s'}, {name:'temp',value:null,unit:'°C'}], [{name:'writes',value:12,unit:'MiB/s'},{name:'temp',value:30,unit:'°C'}]);
  assert.equal(rows.length, 3); assert.ok(rows.every(row => row.delta === null));
  const same = compareMetrics([{name:'CPU',value:55.69,unit:'%'}], [{name:'CPU',value:49.16,unit:'%'}]);
  assert.ok(Math.abs(same[0].delta + 6.53) < 1e-8);
});
const route = load('../app/api/[...path]/route.ts');
test('report proxy preserves private credentials and query filters without caching or redirects', async () => {
  const old = global.fetch, url = process.env.BACKEND_URL;
  process.env.BACKEND_URL = 'http://backend.test:8000';
  global.fetch = async (url, options) => {
    assert.equal(url, 'http://backend.test:8000/api/reports?project=OpenStack&status=PARTIAL&limit=10');
    assert.equal(options.headers.Cookie, 'autolog_admin=test-session');
    assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  };
  try {
    const req = new NextRequest('http://localhost/api/reports?project=OpenStack&status=PARTIAL&limit=10', { headers: {Cookie:'autolog_admin=test-session'} });
    const res = await route.GET(req, {params:Promise.resolve({path:['reports']})});
    assert.equal(res.status, 200); assert.equal(res.headers.get('cache-control'), 'no-store');
  } finally { global.fetch = old; if (url === undefined) delete process.env.BACKEND_URL; else process.env.BACKEND_URL = url; }
});
test('report proxy rejects cookie writes from an unrelated origin', async () => {
  const res = await route.POST(new NextRequest('http://localhost/api/reports', {method:'POST',body:'{}',headers:{'Content-Type':'application/json',Host:'localhost',Origin:'https://evil.example',Cookie:'autolog_admin=test-session'}}), {params:Promise.resolve({path:['reports']})});
  assert.equal(res.status, 403);
});
