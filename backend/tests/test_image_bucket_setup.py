import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sys

import pytest
from botocore.exceptions import ClientError

spec=importlib.util.spec_from_file_location('image_bucket_setup',Path(__file__).parents[2]/'scripts/setup-image-bucket.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


def error(code):return ClientError({'Error':{'Code':code,'Message':'not exposed'}},'GetBucketPolicy')


@pytest.fixture
def client(monkeypatch):
    client=Mock()
    monkeypatch.setattr(setup,'s3_client',lambda:client)
    monkeypatch.setattr(setup,'get_settings',lambda:SimpleNamespace(s3_bucket='autolog-public',s3_region='us-east-1'))
    return client


def run(monkeypatch,*args):
    monkeypatch.setattr(sys,'argv',['setup-image-bucket.py',*args]);setup.main()


def test_existing_bucket_never_mutated_without_flags(client,monkeypatch):
    run(monkeypatch)
    client.create_bucket.assert_not_called();client.put_bucket_policy.assert_not_called()


def test_missing_bucket_requires_create(client,monkeypatch):
    client.head_bucket.side_effect=error('404')
    with pytest.raises(SystemExit):run(monkeypatch)
    client.create_bucket.assert_not_called()
    run(monkeypatch,'--create');client.create_bucket.assert_called_once_with(Bucket='autolog-public')
    client.put_bucket_policy.assert_not_called()


def test_public_read_only_upload_prefix(client,monkeypatch):
    client.get_bucket_policy.side_effect=error('NoSuchBucketPolicy')
    run(monkeypatch,'--public-read')
    policy=json.loads(client.put_bucket_policy.call_args.kwargs['Policy'])
    assert policy['Statement'][0]['Action']==['s3:GetObject']
    assert policy['Statement'][0]['Resource']==['arn:aws:s3:::autolog-public/uploads/*']


def test_different_policy_requires_explicit_replacement(client,monkeypatch):
    client.get_bucket_policy.return_value={'Policy':'{"Statement": []}'}
    with pytest.raises(SystemExit):run(monkeypatch,'--public-read')
    client.put_bucket_policy.assert_not_called()
    run(monkeypatch,'--public-read','--replace-policy')
    client.put_bucket_policy.assert_called_once()


def test_unsupported_policy_fails_closed(client,monkeypatch):
    client.get_bucket_policy.side_effect=error('NotImplemented')
    with pytest.raises(SystemExit,match='Public-read policy was not confirmed'):
        run(monkeypatch,'--public-read')
    client.put_bucket_policy.assert_not_called()


def test_unsupported_put_policy_is_not_reported_as_success(client,monkeypatch):
    client.get_bucket_policy.side_effect=error('NoSuchBucketPolicy')
    client.put_bucket_policy.side_effect=error('NotImplemented')
    with pytest.raises(SystemExit,match='Public-read policy was not confirmed'):
        run(monkeypatch,'--public-read')
