"""Optional integration test: requires Python Playwright, Pillow and Chromium.
Uses real local API/proxy with mock storage; never writes Articles or Garage objects.
Run with backend/.venv/bin/python after frontend build and test DB migrations.
Requires TEST_DATABASE_URL ending in _test; CHROMIUM_PATH optionally selects a browser.
"""
import os,secrets,subprocess,hashlib
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1];password=secrets.token_urlsafe(32);token=secrets.token_urlsafe(48)
from sqlalchemy.engine import make_url
test_url=os.environ.get('TEST_DATABASE_URL', '')
if not test_url or not (make_url(test_url).database or '').endswith('_test'):
 raise RuntimeError('Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test')
env=os.environ.copy();env.update(DATABASE_URL=test_url,APP_USERNAME='upload-test',APP_PASSWORD=password,API_TOKEN=token,S3_PUBLIC_BASE_URL='https://images.example.com',BACKEND_URL='http://127.0.0.1:8101')
code='''import hashlib,os
from app.auth import is_admin
from app.main import app
from app.storage import get_image_storage
from fastapi import HTTPException
seen=[];stored=[]
@app.middleware('http')
async def observe(request,call_next):
 if request.url.path=='/api/uploads' and request.method=='POST':
  raw=await request.body()
  seen.append({'path':request.url.path,'bytes':len(raw),'length':request.headers.get('content-length'),
   'type':request.headers.get('content-type'),'admin':is_admin(request),
   'bearer':request.headers.get('authorization')=='Bearer '+os.environ['API_TOKEN']})
 return await call_next(request)
@app.get('/test-upload-observations')
def observations():return {'seen':seen,'stored':stored}
class Fake:
 def put(self,key,data,mime):
  assert key.startswith('uploads/') and mime.startswith('image/')
  stored.append({'key':key,'sha256':hashlib.sha256(data).hexdigest()})
  if mime=='image/jpeg':raise HTTPException(502,'Image storage is unavailable; please retry')
app.dependency_overrides[get_image_storage]=Fake
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8101,log_level='error')
'''
backend=subprocess.Popen([str(root/'backend/.venv/bin/python'),'-c',code],cwd=root/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
frontend_env=env.copy()
for key in ['API_TOKEN','APP_PASSWORD','APP_USERNAME','DATABASE_URL','S3_ACCESS_KEY','S3_SECRET_KEY']:frontend_env.pop(key,None)
frontend=subprocess.Popen(['node','node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3101'],cwd=root/'frontend',env=frontend_env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
# Valid image larger than the old 1-MiB JSON proxy limit.
png=BytesIO();Image.frombytes('RGB',(900,900),os.urandom(900*900*3)).save(png,format='PNG');png=png.getvalue();assert len(png)>1024*1024
jpg=BytesIO();Image.new('RGB',(4,4),'blue').save(jpg,format='JPEG');jpg=jpg.getvalue()
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),args=['--no-sandbox']);guest=browser.new_context();page=guest.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  for _ in range(30):
   try:page.goto('http://127.0.0.1:3101');break
   except Exception:page.wait_for_timeout(250)
  expect(page.get_by_label('이미지 업로드',exact=True)).to_have_count(0)
  for headers in [{},{'Authorization':'Bearer invalid'}]:
   response=guest.request.post('http://127.0.0.1:3101/api/uploads',multipart={'file':{'name':'a.png','mimeType':'image/png','buffer':png}},headers=headers)
   assert response.status==401
  admin=browser.new_context();page=admin.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://images.example.com/**',lambda r:r.fulfill(status=200,content_type='image/png',body=png))
  page.goto('http://127.0.0.1:3101/login');page.get_by_label('사용자 이름').fill('upload-test');page.get_by_label('비밀번호',exact=True).fill(password);page.get_by_role('button',name='로그인',exact=True).click();expect(page).to_have_url('http://127.0.0.1:3101/admin')
  page.get_by_role('link',name='글 작성',exact=True).click();body=page.get_by_label('본문',exact=True);body.fill('Before\nAfter');body.evaluate('(e)=>{e.focus(); e.setSelectionRange(7,7);e.dispatchEvent(new Event("select",{bubbles:true}));}')
  page.get_by_label('이미지 설명',exact=True).fill('OpenStack [구성도]')
  with page.expect_response(lambda r:r.url.endswith('/api/uploads') and r.request.method=='POST') as response:
   page.get_by_label('이미지 업로드',exact=True).set_input_files({'name':'../../x.png','mimeType':'image/png','buffer':png})
  assert response.value.status==201
  result=response.value.json()
  observation=guest.request.get('http://127.0.0.1:8101/test-upload-observations').json()
  reached=observation['seen'][-1]
  assert reached['path']=='/api/uploads' and reached['admin']
  assert reached['type']==response.value.request.headers['content-type']
  assert int(reached['length'])==reached['bytes']
  assert observation['stored'][-1]['sha256']==hashlib.sha256(png).hexdigest()
  expected='Before\n\n![OpenStack \\[구성도\\]]('+result['url']+')\nAfter'
  expect(body).to_have_value(expected)
  snippet=page.get_by_label('최근 업로드 이미지 Markdown',exact=True)
  expect(snippet).to_have_value(expected[len('Before\n'): -len('After')].strip())
  # Same bytes under another filename reuse the successful URL, preserving selected text.
  before_repeat=len(observation['seen'])
  body.evaluate('(e)=>{e.focus();e.setSelectionRange(0,6);e.dispatchEvent(new Event("select",{bubbles:true}));}')
  page.get_by_label('이미지 업로드',exact=True).set_input_files({'name':'same-bytes.png','mimeType':'image/png','buffer':png})
  expect(body).to_have_value('\n'+snippet.input_value()+'\n'+expected)
  assert len(guest.request.get('http://127.0.0.1:8101/test-upload-observations').json()['seen'])==before_repeat
  body.fill(expected)
  # Deterministic clipboard success/failure without granting OS clipboard access.
  page.evaluate('''() => { Object.defineProperty(navigator, 'clipboard', {configurable: true,
   value: {writeText: async text => {window.copiedImageMarkdown=text}}}); }''')
  page.get_by_role('button',name='이미지 Markdown 복사',exact=True).click()
  expect(page.get_by_role('status')).to_contain_text('Markdown을 복사했습니다')
  assert page.evaluate('window.copiedImageMarkdown')==snippet.input_value()
  page.evaluate('''() => { Object.defineProperty(navigator, 'clipboard', {configurable: true,
   value: {writeText: async () => {throw new Error('denied')}}}); }''')
  page.get_by_role('button',name='이미지 Markdown 복사',exact=True).click()
  expect(page.get_by_role('status')).to_contain_text('직접 선택해 복사')
  page.get_by_role('button',name='미리보기',exact=True).click();expect(page.get_by_role('img',name='OpenStack [구성도]',exact=True)).to_have_attribute('src',result['url'])
  page.get_by_role('button',name='편집하기',exact=True).click()
  with page.expect_response(lambda r:r.url.endswith('/api/uploads') and r.request.method=='POST') as response:
   page.get_by_label('이미지 업로드',exact=True).set_input_files({'name':'x.jpg','mimeType':'image/jpeg','buffer':jpg})
  assert response.value.status==502;expect(snippet).to_have_value(page.evaluate('window.copiedImageMarkdown'));expect(page.locator('p[role=alert]')).to_contain_text('Image storage is unavailable');expect(body).to_have_value(expected)
  page.get_by_label('이미지 업로드',exact=True).set_input_files({'name':'x.svg','mimeType':'image/svg+xml','buffer':b'<svg/>'});expect(page.locator('p[role=alert]')).to_contain_text('JPEG, PNG')
  csrf=admin.request.post('http://127.0.0.1:3101/api/uploads',multipart={'file':{'name':'x.png','mimeType':'image/png','buffer':png}},headers={'Origin':'https://evil.example'});assert csrf.status==403
  bearer=guest.request.post('http://127.0.0.1:3101/api/uploads',multipart={'file':{'name':'x.png','mimeType':'image/png','buffer':png}},headers={'Authorization':'Bearer '+token});assert bearer.status==201
  observation=guest.request.get('http://127.0.0.1:8101/test-upload-observations').json()
  assert observation['seen'][-1]['bearer'] and not observation['seen'][-1]['admin']
  assert observation['stored'][-1]['sha256']==hashlib.sha256(png).hexdigest()
  before=len(observation['seen'])
  oversized=guest.request.post('http://127.0.0.1:3101/api/uploads',multipart={'file':{'name':'huge.png','mimeType':'image/png','buffer':b'x'*(11*1024*1024)}},headers={'Authorization':'Bearer '+token})
  assert oversized.status==413 and oversized.headers['content-type'].startswith('application/json')
  assert len(guest.request.get('http://127.0.0.1:8101/test-upload-observations').json()['seen'])==before
  assert guest.request.get('http://127.0.0.1:3101/api/categories').status==200
  assert not errors,errors
  (root/'.deploy').mkdir(exist_ok=True)
  page.screenshot(path=str(root/'.deploy/autolog-image-upload-ui.png'),full_page=True)
  browser.close()
 print('PASS: real frontend proxy/backend validation with mocked storage; backend POST reachability/boundary/Content-Length/file digest, admin upload >1MiB, cursor insertion, escaped alt, Markdown copy success/fallback, Markdown image preview, storage failure preserving text, SVG rejection, anonymous/token auth and CSRF. No DB writes or real Garage access.')
finally:
 for process in [frontend,backend]:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
