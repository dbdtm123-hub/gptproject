"""Actual upload HTTP parsing/guards/decoder with mocked S3, no DB or worker IPC.

Only thread scheduling is replaced because this executor prohibits its socket
wakeup. This is an ASGI integration test, not a Garage network test.
"""
import asyncio
from io import BytesIO
import os
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import EndpointConnectionError
from fastapi import Request
import httpx
from PIL import Image
import pytest
import starlette.datastructures

from app import storage, uploads
from app.auth import bearer, require_write_access
from app.main import app


@pytest.fixture
def upload_api(monkeypatch):
    s3 = Mock()
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(s3_public_base_url='https://img.example.com', s3_bucket='existing-bucket'))
    monkeypatch.setattr(storage, 's3_client', lambda: s3)
    async def immediate(fn, *args): return fn(*args)
    async def authorize(request: Request): require_write_access(request, await bearer(request))
    async def get_storage(): return storage.ImageStorage()
    monkeypatch.setattr(uploads, 'run_in_threadpool', immediate)
    monkeypatch.setattr(starlette.datastructures, 'run_in_threadpool', immediate)
    app.dependency_overrides[require_write_access] = authorize
    app.dependency_overrides[storage.get_image_storage] = get_storage
    yield s3
    app.dependency_overrides.pop(require_write_access, None)
    app.dependency_overrides.pop(storage.get_image_storage, None)


def post(data=b'', mime='image/png', token=True, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.example.com') as client:
            return await client.post('/api/uploads', headers={'Authorization': 'Bearer '+os.environ['API_TOKEN']} if token else {},
                                     **({'files': {'file': ('../../unsafe', data, mime)}} if not kwargs else kwargs))
    return asyncio.run(run())


@pytest.mark.parametrize('format,mime,extension', [('PNG','image/png','png'),('JPEG','image/jpeg','jpg'),('WEBP','image/webp','webp'),('GIF','image/gif','gif')])
def test_real_multipart_decoder_and_storage_bytes(upload_api, format, mime, extension):
    image=BytesIO(); Image.new('RGB',(2,2),'green').save(image,format=format); data=image.getvalue()
    response=post(data,mime)
    assert response.status_code==201
    result=response.json(); assert result['key'].endswith('.'+extension)
    assert result['url']=='https://img.example.com/'+result['key']
    assert upload_api.put_object.call_args.kwargs['Body']==data
    assert upload_api.put_object.call_args.kwargs['ContentType']==mime


@pytest.mark.parametrize('data,mime,status', [(b'fake','image/png',422),(b'<svg/>','image/svg+xml',415),(b'','image/png',422), (b'x'*(10*1024*1024+1),'image/png',413)])
def test_real_upload_rejects_bad_or_oversized_input(upload_api,data,mime,status):
    assert post(data,mime).status_code==status
    upload_api.put_object.assert_not_called()


def test_unauthenticated_upload_never_consumes_body(upload_api):
    async def never():
        raise AssertionError('unauthenticated body must not be read')
        yield b'not reachable'
    assert post(token=False, content=never()).status_code==401
    upload_api.put_object.assert_not_called()


def test_real_upload_s3_error_is_sanitized(upload_api):
    image=BytesIO(); Image.new('RGB',(2,2),'green').save(image,format='PNG')
    upload_api.put_object.side_effect=EndpointConnectionError(endpoint_url='http://internal-secret-endpoint')
    response=post(image.getvalue())
    assert response.status_code==502
    assert 'internal-secret-endpoint' not in response.text
