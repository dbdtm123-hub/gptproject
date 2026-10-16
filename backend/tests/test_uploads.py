from io import BytesIO
import re
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from botocore.exceptions import ClientError, EndpointConnectionError
from pydantic import SecretStr

from app.main import app
from app import storage, uploads


def image_bytes(format='PNG'):
    output=BytesIO();Image.new('RGB',(4,4),'green').save(output,format=format)
    return output.getvalue()


@pytest.fixture
def upload_storage(monkeypatch):
    client=Mock()
    settings=SimpleNamespace(s3_public_base_url='https://img.myhomlab.win',s3_bucket='autolog-public')
    monkeypatch.setattr(storage,'get_settings',lambda:settings)
    monkeypatch.setattr(storage,'s3_client',lambda:client)
    yield client


@pytest.mark.parametrize('header',[None,'Bearer invalid'])
def test_guests_and_invalid_token_rejected_before_body(header,upload_storage):
    with TestClient(app) as visitor:
        response=visitor.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')},headers={'Authorization':header} if header else {})
        assert response.status_code==401
        # Even malformed huge uploads must fail auth first, without calling storage.
        assert visitor.post('/api/uploads',content=b'bad',headers={'Content-Length':'999999999'}).status_code==401
    upload_storage.put_object.assert_not_called()


@pytest.mark.parametrize('format,mime,extension',[('PNG','image/png','png'),('JPEG','image/jpeg','jpg'),('WEBP','image/webp','webp'),('GIF','image/gif','gif')])
def test_token_upload_safe_key_and_public_url(client,upload_storage,format,mime,extension):
    cookies=dict(client.cookies);client.cookies.clear()
    try:response=client.post('/api/uploads',files={'file':('../../payload.exe',image_bytes(format),mime)})
    finally:client.cookies.update(cookies)
    assert response.status_code==201
    result=response.json();key=result['key']
    assert re.fullmatch(r'uploads/\d{4}/\d{2}/[0-9a-f]{32}\.'+extension,key)
    assert UUID(key.split('/')[-1].split('.')[0]).version == 4
    assert result['url']=='https://img.myhomlab.win/'+key and 'svc.cluster.local' not in result['url']
    args=upload_storage.put_object.call_args.kwargs
    assert args['Key']==key and args['Bucket']=='autolog-public' and args['ContentType']==mime
    assert 'ACL' not in args


def test_admin_cookie_and_csrf(client,upload_storage):
    headers=dict(client.headers);client.headers.pop('authorization',None)
    try:
        assert client.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')}).status_code==201
        assert client.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')},headers={'Origin':'https://evil.example'}).status_code==403
    finally:client.headers.update(headers)
    assert upload_storage.put_object.call_count==1


@pytest.mark.parametrize('body,mime,status',[(b'<svg/>','image/svg+xml',415),(b'MZ executable','image/png',422),(image_bytes(),'image/jpeg',415),(b'','image/png',422),(image_bytes()[:20],'image/png',422)])
def test_invalid_image_rejected(client,upload_storage,body,mime,status):
    assert client.post('/api/uploads',files={'file':('evil',body,mime)}).status_code==status
    upload_storage.put_object.assert_not_called()


def test_oversize_and_chunked_body_cap(client,upload_storage):
    assert client.post('/api/uploads',files={'file':('huge.png',b'x'*(uploads.MAX_IMAGE_BYTES+1),'image/png')}).status_code==413
    boundary='image-test'
    def chunks():
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        for _ in range(12):yield b'x'*(1024*1024)
        yield f'\r\n--{boundary}--\r\n'.encode()
    result=client.post('/api/uploads',content=chunks(),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    assert result.status_code==413
    upload_storage.put_object.assert_not_called()


@pytest.mark.parametrize('error',[EndpointConnectionError(endpoint_url='http://internal'),ClientError({'Error':{'Code':'AccessDenied','Message':'secret-never-return'}},'PutObject')])
def test_storage_failure_is_safe_5xx(client,upload_storage,error):
    upload_storage.put_object.side_effect=error
    result=client.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')})
    assert result.status_code==502 and 'secret-never-return' not in result.text and 'internal' not in result.text


@pytest.mark.parametrize('url',[None,'http://garage.lifelog.svc.cluster.local:3900','https://garage.lifelog.svc.cluster.local:3900','https://127.0.0.1','https://img.example.com?secret=bad'])
def test_bad_public_url_does_not_store(client,upload_storage,monkeypatch,url):
    monkeypatch.setattr(storage,'get_settings',lambda:SimpleNamespace(s3_public_base_url=url))
    assert client.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')}).status_code==503
    upload_storage.put_object.assert_not_called()


def test_pixel_budget(client,upload_storage,monkeypatch):
    monkeypatch.setattr(uploads,'MAX_DECODE_PIXELS',1)
    assert client.post('/api/uploads',files={'file':('x.png',image_bytes(),'image/png')}).status_code==422
    upload_storage.put_object.assert_not_called()


def test_sdk_adapter_uses_internal_endpoint_and_path_style(monkeypatch):
    settings=SimpleNamespace(s3_endpoint='http://garage.lifelog.svc.cluster.local:3900',s3_access_key=SecretStr('test-access'),s3_secret_key=SecretStr('test-secret'),s3_region='us-east-1')
    monkeypatch.setattr(storage,'get_settings',lambda:settings)
    mock=Mock();monkeypatch.setattr(storage.boto3,'client',mock)
    storage.s3_client();kwargs=mock.call_args.kwargs
    assert kwargs['endpoint_url']==settings.s3_endpoint
    assert kwargs['config'].s3['addressing_style']=='path'
    settings.s3_access_key=None
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:storage.s3_client()
    assert error.value.status_code==503
