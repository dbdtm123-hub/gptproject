import hashlib
import hmac
import json
import os
import time
from fastapi.testclient import TestClient
from app.auth import COOKIE_NAME, create_session, encode, session_key
from app.main import app


def data(slug, status='draft', **extra):
    return dict(title='Private' if status=='draft' else 'Public', slug=slug, category='private' if status=='draft' else 'public',
                summary='summary', content_markdown='# Article', tags=[status], status=status, **extra)


def test_public_reads_hide_every_draft_surface(client):
    draft = client.post('/api/articles', json=data('private')).json()
    pub = client.post('/api/articles', json=data('public', 'published', related_articles=[draft['id']], source_reference='private conversation')).json()
    with TestClient(app) as visitor:
        listing=visitor.get('/api/articles').json()
        assert listing['total']==1 and listing['items'][0]['id']==pub['id']
        assert visitor.get('/api/articles', params={'slug':'private'}).json()['total']==0
        assert visitor.get('/api/articles', params={'q':'Private'}).json()['total']==0
        assert visitor.get('/api/articles', params={'status':'draft'}).status_code==401
        assert visitor.get('/api/articles/'+draft['id']).status_code==401
        article=visitor.get('/api/articles/'+pub['id']).json()
        assert article['content_markdown']=='# Article' and article['related_articles']==[]
        assert article['source_reference'] is None
        assert [item['name'] for item in visitor.get('/api/categories').json()]==['public']
        assert visitor.get('/api/ingest/categories').json()==visitor.get('/api/categories').json()
        assert visitor.get('/api/tags').json()==['published']
        assert visitor.get('/api/entries').status_code==401
        # A valid writing token never upgrades GET to an administrator.
        visitor.headers['Authorization']='Bearer '+os.environ['API_TOKEN']
        assert visitor.get('/api/articles').json()['total']==1
        assert visitor.get('/api/articles/'+draft['id']).status_code==401
        assert visitor.get('/api/entries').status_code==401


def test_guest_all_write_methods_rejected(client):
    draft=client.post('/api/articles',json=data('private')).json()
    with TestClient(app) as visitor:
        assert visitor.post('/api/articles',json=data('attack')).status_code==401
        assert visitor.post('/api/articles/upsert',json=data('attack')).status_code==401
        assert visitor.patch('/api/articles/'+draft['id'],json={'status':'published'}).status_code==401
        assert visitor.delete('/api/articles/'+draft['id']).status_code==401
        visitor.headers['Authorization']='Bearer invalid'
        assert visitor.post('/api/articles',json=data('attack')).status_code==401
    assert client.get('/api/articles/'+draft['id']).json()['status']=='draft'


def test_login_admin_crud_and_token_write(client):
    with TestClient(app) as admin:
        bad=admin.post('/api/auth/login',json={'username':os.environ['APP_USERNAME'],'password':'wrong'})
        assert bad.status_code==401
        login=admin.post('/api/auth/login',json={'username':os.environ['APP_USERNAME'],'password':os.environ['APP_PASSWORD']})
        assert login.status_code==200 and os.environ['API_TOKEN'] not in login.text
        admin.cookies.set(COOKIE_NAME,login.json()['session'])
        assert admin.get('/api/auth/me').status_code==200
        draft=admin.post('/api/articles',json=data('private')).json()
        assert admin.get('/api/articles/'+draft['id']).status_code==200
        assert admin.get('/api/articles',params={'status':'draft'}).json()['total']==1
        assert admin.patch('/api/articles/'+draft['id'],json={'status':'published'}).status_code==200
        assert admin.delete('/api/articles/'+draft['id']).status_code==204
    with TestClient(app,headers={'Authorization':'Bearer '+os.environ['API_TOKEN']}) as token:
        result=token.post('/api/articles',json=data('chatgpt'))
        assert result.status_code==201
        assert token.get('/api/articles/'+result.json()['id']).status_code==401
        assert token.patch('/api/articles/'+result.json()['id'],json={'status':'published'}).status_code==200
        assert token.delete('/api/articles/'+result.json()['id']).status_code==204


def test_invalid_expired_and_forged_sessions_and_csrf(client):
    for value in ['garbage',create_session()+'bad']:
        with TestClient(app,cookies={COOKIE_NAME:value}) as invalid:
            assert invalid.get('/api/auth/me').status_code==401
            assert invalid.post('/api/articles',json=data('attack')).status_code==401
    payload=encode(json.dumps({'user':os.environ['APP_USERNAME'],'exp':int(time.time())-1}).encode())
    expired=payload+'.'+encode(hmac.new(session_key(),payload.encode(),hashlib.sha256).digest())
    with TestClient(app,cookies={COOKIE_NAME:expired}) as expired_client:
        assert expired_client.get('/api/auth/me').status_code==401
    with TestClient(app,cookies={COOKIE_NAME:create_session()}) as admin:
        assert admin.post('/api/articles',json=data('csrf'),headers={'Origin':'https://evil.example'}).status_code==403
        assert admin.get('/api/articles').json()['total']==0


def test_login_rate_limit(client):
    from app.auth import login_failures
    login_failures.clear()
    try:
        for _ in range(10):
            assert client.post('/api/auth/login',json={'username':'wrong','password':'wrong'}).status_code==401
        response=client.post('/api/auth/login',json={'username':'wrong','password':'wrong'})
        assert response.status_code==429 and response.headers['retry-after']=='60'
    finally:
        login_failures.clear()
