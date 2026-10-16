"""Explicit Garage setup, not run at app startup. Credentials come only from environment.

Run from backend using its installed Python:
  ../scripts/setup-image-bucket.py --create --public-read
Changing policy requires explicit --public-read; replacing a different existing
policy also requires --replace-policy. Upload requests never mutate bucket policy.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from app.config import get_settings
from app.storage import s3_client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create',action='store_true')
    parser.add_argument('--public-read',action='store_true')
    parser.add_argument('--replace-policy',action='store_true')
    args=parser.parse_args()
    try: client=s3_client()
    except HTTPException:raise SystemExit("S3 credentials are not configured in the backend environment.") from None
    settings=get_settings();bucket=settings.s3_bucket
    policy={'Version':'2012-10-17','Statement':[{'Sid':'ReadUploadedImages','Effect':'Allow',
        'Principal':'*','Action':['s3:GetObject'],'Resource':[f'arn:aws:s3:::{bucket}/uploads/*']}]}
    try:
        try: client.head_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response.get('Error',{}).get('Code') not in {'404','NoSuchBucket','NotFound'}:raise
            if not args.create:raise SystemExit('Bucket is missing; rerun with --create after reviewing the target.')
            kwargs={'Bucket':bucket}
            if settings.s3_region!='us-east-1':kwargs['CreateBucketConfiguration']={'LocationConstraint':settings.s3_region}
            client.create_bucket(**kwargs)
        if args.public_read:
            try: existing=json.loads(client.get_bucket_policy(Bucket=bucket)['Policy'])
            except ClientError as error:
                if error.response.get('Error',{}).get('Code') not in {'NoSuchBucketPolicy','NoSuchPolicy'}:raise
                existing=None
            if existing is not None and existing!=policy and not args.replace_policy:
                raise SystemExit('Existing policy differs; review it and use --replace-policy only if replacement is intended.')
            client.put_bucket_policy(Bucket=bucket,Policy=json.dumps(policy))
        print('Bucket setup complete. Anonymous permissions are GetObject on uploads/* only when --public-read is used.')
    except ClientError as error:
        code=error.response.get('Error',{}).get('Code','')
        if code in {'NotImplemented','501','UnsupportedOperation','MethodNotAllowed'}:
            raise SystemExit('This S3 server does not support the requested bucket/policy API. Public-read policy was not confirmed. Use a read-only signing gateway; do not assume anonymous access.') from None
        raise SystemExit('Bucket setup failed. Check credentials, endpoint and network access; no credential values were printed.') from None
    except BotoCoreError:
        raise SystemExit('Bucket setup failed. Check credentials, endpoint and network access; no credential values were printed.') from None


if __name__=='__main__': main()
