import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker/index.js';
const records=new Map(),calls=[];
const env={CREDENTIAL_ENCRYPTION_KEY:Buffer.alloc(32,4).toString('base64'),BUCKET:{async put(k,v){records.set(k,v)},async get(k){const v=records.get(k);return v?{async json(){return JSON.parse(v)}}:null}}};
const origin='https://autolog.test';
const token='test-existing-api-token-with-more-than-32-characters';
let failStatus=0;
globalThis.fetch=async(url,opts)=>{calls.push({url,opts});if(failStatus)return Response.json({detail:'Conflict'}, {status:failStatus});return Response.json({report_id:'OPENSTACK-RABBITMQ-0020',revision:1})};
function req(path,data,uid='alice'){return new Request(origin+path,{method:'POST',headers:{'content-type':'application/json',origin,...(uid?{'oai-authenticated-user-id':uid}:{})},body:JSON.stringify(data)})}
async function rpc(name,args={},uid='alice'){return worker.fetch(req('/mcp',{jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}},uid),env)}
const fields={report_id:'OPENSTACK-RABBITMQ-0020',title:'Report',project:'OpenStack',component:'RabbitMQ',author:'Codex',source:'Codex',status:'PARTIAL',summary:'Measured',raw_markdown:'# Actual report',metrics_before:[{name:'CPU',value:52.3,unit:'percent'}]};
test('Report catalog exposes all eight tools and correct annotations',async()=>{
 const r=await worker.fetch(req('/mcp',{jsonrpc:'2.0',id:1,method:'tools/list'}),env);const tools=(await r.json()).result.tools;
 for(const name of ['search_reports','get_report','create_report','get_latest_project_report','list_reports','update_report','get_unresolved_issues','get_report_history'])assert.ok(tools.some(t=>t.name===name));
 assert.equal(tools.find(t=>t.name==='get_report').annotations.readOnlyHint,true);assert.equal(tools.find(t=>t.name==='create_report').annotations.readOnlyHint,false);
 assert.ok(!JSON.stringify(tools).includes('_route'));assert.ok(!JSON.stringify(tools).includes('$ref'));
});
test('Report calls require Site identity and that user own credential',async()=>{
 assert.equal((await rpc('get_report',{report_id:fields.report_id},null)).status,401);
 const before=calls.length;assert.equal((await (await rpc('get_report',{report_id:fields.report_id},'bob')).json()).result.isError,true);assert.equal(calls.length,before);
 await worker.fetch(req('/api/connection',{token}),env);assert.ok(![...records.values()].some(x=>x.includes(token)));
});
test('Reads route to private backend with preserved token and encoded filters',async()=>{
 for(const [name,args,path] of [['get_report',{report_id:fields.report_id},'/api/reports/'+fields.report_id],['get_latest_project_report',{project:'OpenStack',component:'RabbitMQ'},'/api/reports/latest?project=OpenStack&component=RabbitMQ'],['search_reports',{q:'CPU & disk',project:'OpenStack',status:'PARTIAL',limit:5,offset:10},'/api/reports?project=OpenStack&q=CPU+%26+disk&status=PARTIAL&limit=5&offset=10'],['get_report_history',{report_id:fields.report_id,limit:10},'/api/reports/'+fields.report_id+'/history?limit=10'],['get_unresolved_issues',{},'/api/reports/unresolved'],['list_reports',{},'/api/reports']]){
  const r=(await (await rpc(name,args)).json()).result;assert.ok(!r.isError);const sent=calls.at(-1);assert.equal(sent.url, 'https://autolog.myhomlab.win'+path);assert.equal(sent.opts.method,'GET');assert.equal(sent.opts.headers.Authorization,'Bearer '+token);
 }
});
test('Report writes preserve stable ID and revision and never route to articles',async()=>{
 await rpc('create_report',fields);let sent=calls.at(-1);assert.equal(sent.opts.method,'POST');assert.equal(sent.url,'https://autolog.myhomlab.win/api/reports');assert.deepEqual(JSON.parse(sent.opts.body),fields);
 await rpc('update_report',{...fields,expected_revision:1});sent=calls.at(-1);assert.equal(sent.opts.method,'PATCH');assert.equal(sent.url,'https://autolog.myhomlab.win/api/reports/'+fields.report_id);assert.equal(JSON.parse(sent.opts.body).expected_revision,1);assert.ok(!('report_id' in JSON.parse(sent.opts.body)));
});
test('Invalid IDs, pagination and missing revision are rejected before upstream',async()=>{
 for(const [name,args] of [['get_report',{report_id:'../../articles'}],['search_reports',{limit:101}],['search_reports',{offset:-1}],['search_reports',{url:'https://evil.test'}],['update_report',fields]]){
 const before=calls.length;assert.equal((await (await rpc(name,args)).json()).result.isError,true);assert.equal(calls.length,before);
 }
});
test('Conflicts and invalid credentials are returned without write retries',async()=>{
 for(const status of [401,403,409]){failStatus=status;const before=calls.length;assert.equal((await (await rpc('create_report',fields)).json()).result.isError,true);assert.equal(calls.length,before+1)}failStatus=0;
});

test('Different Site users forward their own credentials, never another user token',async()=>{
 const bobToken='bob-private-report-reader-token-more-than-32-characters';
 await worker.fetch(req('/api/connection',{token:bobToken},'bob'),env);
 await rpc('get_report',{report_id:fields.report_id},'bob');
 assert.equal(calls.at(-1).opts.headers.Authorization,'Bearer '+bobToken);
 await rpc('get_report',{report_id:fields.report_id},'alice');
 assert.equal(calls.at(-1).opts.headers.Authorization,'Bearer '+token);
});
