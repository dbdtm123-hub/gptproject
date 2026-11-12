import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker/index.js';
const records=new Map();
const env={CREDENTIAL_ENCRYPTION_KEY:Buffer.alloc(32,4).toString('base64'),BUCKET:{async put(k,v){records.set(k,v);},async get(k){const v=records.get(k);return v?{async json(){return JSON.parse(v);}}:null;},async delete(k){records.delete(k);}}};
const origin='https://autolog.test';
const calls=[];
globalThis.fetch=async(url,opts={})=>{calls.push({url,opts});if(url.includes('/api/categories'))return Response.json([{id:'category',name:'홈랩'}]);if(url.includes('?slug='))return Response.json({items:[{id:'12345678-1234-1234-1234-123456789012'}]});return Response.json({id:'12345678-1234-1234-1234-123456789012',title:'설정 완료'});};
function request(path,method='GET',data,uid='alice',extra={}){return new Request(origin+path,{method,headers:{...(uid?{'oai-authenticated-user-id':uid}:{}),'content-type':'application/json',...(method!=='GET'?{origin}:{}),...extra},body:data===undefined?undefined:JSON.stringify(data)});}
async function rpc(method,params={},uid='alice'){return worker.fetch(request('/mcp','POST',{jsonrpc:'2.0',id:1,method,params},uid),env);}
test('MCP discovery uses actual AutoLog schemas without private data',async()=>{
 const init=await (await rpc('initialize',{protocolVersion:'2025-03-26'},null)).json();assert.equal(init.result.protocolVersion,'2025-03-26');
 const result=await (await rpc('tools/list',{},null)).json();assert.equal(result.result.tools.length,18);assert.equal(result.result.tools.find(t=>t.name==='create_article').inputSchema.required.includes('content_markdown'),true);assert.ok(!JSON.stringify(result).includes('_route'));
});
test('Data operations require identity',async()=>{assert.equal((await rpc('tools/call',{name:'search_articles',arguments:{}},null)).status,401);});
test('Connection stores encrypted token scoped to signed-in user',async()=>{
 const token='private-autolog-token-that-is-at-least-32-characters';
 const r=await worker.fetch(request('/api/connection','POST',{token}),env);assert.equal(r.status,200);assert.equal((await r.json()).connected,true);
 assert.ok([...records.values()].every(v=>!v.includes(token)));
 const other=await (await worker.fetch(request('/api/connection','GET',undefined,'bob'),env)).json();assert.equal(other.configured,false);
 const check=await (await rpc('tools/call',{name:'check_connection'})).json();assert.equal(check.result.structuredContent.connected,true);
});
test('Cross-origin settings writes are rejected',async()=>{const r=await worker.fetch(request('/api/connection','POST',{token:'x'.repeat(40)},'alice',{origin:'https://other.test'}),env);assert.equal(r.status,403);});
test('Search arguments are query encoded and unknown fields rejected',async()=>{
 const r=await (await rpc('tools/call',{name:'search_articles',arguments:{q:'NFS & Ceph',limit:5}})).json();assert.ok(!r.result.isError);assert.ok(calls.at(-1).url.includes('q=NFS+%26+Ceph'));
 const bad=await (await rpc('tools/call',{name:'search_articles',arguments:{url:'https://other.test'}})).json();assert.equal(bad.result.isError,true);
 const range=await (await rpc('tools/call',{name:'search_articles',arguments:{limit:500}})).json();assert.equal(range.result.isError,true);
});
test('Existing slug upsert cannot overwrite without version',async()=>{
 const before=calls.length;
 const r=await (await rpc('tools/call',{name:'upsert_article',arguments:{title:'NFS',slug:'nfs',category:'홈랩',summary:'요약',content_markdown:'# NFS'}})).json();assert.equal(r.result.isError,true);assert.ok(calls.slice(before).every(c=>c.opts.method==='GET'));
});
test('Write body preserves verified fields and concurrency token',async()=>{
 const r=await (await rpc('tools/call',{name:'update_article',arguments:{article_id:'12345678-1234-1234-1234-123456789012',content_markdown:'# 수정',expected_updated_at:'2026-10-09T15:00:00Z'}})).json();assert.ok(!r.result.isError);
 const sent=calls.at(-1);assert.equal(sent.opts.method,'PATCH');const body=JSON.parse(sent.opts.body);assert.ok(!('article_id' in body));assert.equal(body.expected_updated_at,'2026-10-09T15:00:00Z');
});
test('Removing connection deletes only that user credential',async()=>{await worker.fetch(request('/api/connection','DELETE'),env);assert.equal(records.size,0);});

test("Writing guidelines preserve the user style before token configuration",async()=>{const r=await (await rpc("tools/call",{name:"get_writing_guidelines"},"bob")).json();const guide=r.result.structuredContent.guidelines;assert.ok(guide.includes("문제 → 원인 → 해결"));assert.ok(guide.includes("<API_TOKEN>"));assert.ok(guide.includes("실행 성공이 확인된"));assert.ok(guide.includes("일반적인 기술 질문에서는 API에 임의로 글을 작성하지 않는다"));const init=await (await rpc("initialize",{},null)).json();assert.ok(init.result.instructions.startsWith(guide));});
