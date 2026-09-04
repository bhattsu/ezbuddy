import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional
import logging

from app.adapters.aws_clients import aws_clients
from app.config.settings import settings

logger = logging.getLogger(__name__)

class S3Manager:
    """Async S3 file operations"""
    
    def __init__(self):
        self.s3_client = aws_clients.get_s3()
        self.bucket_name = settings.BUCKET_NAME
    
    async def upload_file(
        self,
        file_path: str,
        document_id: str,
        file_name: Optional[str] = None
    ) -> str:
        """Upload file to S3 asynchronously"""
        
        if not file_name:
            file_name = os.path.basename(file_path)
        
        s3_key = f"{settings.S3_PREFIX}/{document_id}/{file_name}"
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.upload_file(
                    Filename=file_path,
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    ExtraArgs={
                        'ServerSideEncryption': 'AES256',
                        'Metadata': {
                            'document-id': document_id,
                            'upload-time': datetime.utcnow().isoformat()
                        }
                    }
                )
            )
            
            logger.info(f"Uploaded to S3: s3://{self.bucket_name}/{s3_key}")
            return s3_key
            
        except Exception as e:
            logger.error(f"Failed to upload to S3: {str(e)}")
            raise RuntimeError(f"S3 upload failed: {str(e)}")
    
    async def upload_file_at_key(
        self,
        file_path: str,
        s3_key: str,
        *,
        bucket: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Upload a local file to an explicit S3 key; return the key."""
        target_bucket = bucket or self.bucket_name
        if not target_bucket:
            raise RuntimeError("BUCKET_NAME is not configured for S3 upload")

        extra: dict = {"ServerSideEncryption": "AES256"}
        if content_type:
            extra["ContentType"] = content_type

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.upload_file(
                    Filename=file_path,
                    Bucket=target_bucket,
                    Key=s3_key,
                    ExtraArgs=extra,
                ),
            )
            logger.info("Uploaded to S3: s3://%s/%s", target_bucket, s3_key)
            return s3_key
        except Exception as e:
            logger.error("Failed to upload to S3 key %s: %s", s3_key, e)
            raise RuntimeError(f"S3 upload failed: {e}") from e

    async def download_bytes(self, s3_key: str, bucket: Optional[str] = None) -> bytes:
        """Download an S3 object into memory and return raw bytes."""
        target_bucket = bucket or self.bucket_name
        if not target_bucket:
            raise RuntimeError("BUCKET_NAME is not configured for S3 download")

        # Allow full s3://bucket/key URIs
        key = s3_key
        if s3_key.startswith("s3://"):
            without = s3_key[5:]
            parts = without.split("/", 1)
            if len(parts) == 2:
                target_bucket, key = parts[0], parts[1]

        try:
            loop = asyncio.get_event_loop()

            def _get() -> bytes:
                obj = self.s3_client.get_object(Bucket=target_bucket, Key=key)
                return obj["Body"].read()

            data = await loop.run_in_executor(None, _get)
            logger.info("Downloaded %d bytes from s3://%s/%s", len(data), target_bucket, key)
            return data
        except Exception as e:
            logger.error("Failed to download bytes from S3: %s", e)
            raise RuntimeError(f"S3 download failed: {e}") from e
    
    async def delete_file(self, s3_key: str):
        """Delete file from S3 asynchronously"""
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=s3_key
                )
            )
            
            logger.info(f"Deleted from S3: {s3_key}")
            
        except Exception as e:
            logger.warning(f"Failed to delete from S3: {str(e)}")
    
    async def get_presigned_url(
        self,
        s3_key: str,
        expiration: int = 3600
    ) -> str:
        """Generate presigned URL for S3 object"""
        
        try:
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': self.bucket_name,
                        'Key': s3_key
                    },
                    ExpiresIn=expiration
                )
            )
            
            logger.info(f"Generated presigned URL for: {s3_key}")
            return url
            
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {str(e)}")
            raise RuntimeError(f"Presigned URL generation failed: {str(e)}")
    
    async def file_exists(self, s3_key: str) -> bool:
        """Check if file exists in S3"""
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(
                    Bucket=self.bucket_name,
                    Key=s3_key
                )
            )
            return True
            
        except:
            return False
    
    async def cleanup_old_files(self, days: int = None):
        """Delete files older than specified days"""
        
        if days is None:
            days = settings.S3_TEMP_EXPIRY_DAYS
        
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=settings.S3_PREFIX
                )
            )
            
            if 'Contents' not in response:
                logger.info("No files to cleanup")
                return
            
            delete_keys = []
            for obj in response['Contents']:
                if obj['LastModified'].replace(tzinfo=None) < cutoff_date:
                    delete_keys.append({'Key': obj['Key']})
            
            if delete_keys:
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.delete_objects(
                        Bucket=self.bucket_name,
                        Delete={'Objects': delete_keys}
                    )
                )
                logger.info(f"Cleaned up {len(delete_keys)} old files from S3")
            
        except Exception as e:
            logger.error(f"Failed to cleanup old S3 files: {str(e)}")

    async def list_objects(
        self,
        prefix: str,
        *,
        bucket: Optional[str] = None,
        max_keys: int = 50,
    ) -> list[dict[str, object]]:
        """List objects under a prefix (for scripts / diagnostics)."""
        target_bucket = bucket or self.bucket_name
        if not target_bucket:
            raise RuntimeError("BUCKET_NAME is not configured for S3 list")

        normalized = prefix.strip("/")
        if normalized:
            normalized = f"{normalized}/"

        try:
            loop = asyncio.get_event_loop()

            def _list() -> list[dict[str, object]]:
                client = self.s3_client
                paginator = client.get_paginator("list_objects_v2")
                out: list[dict[str, object]] = []
                for page in paginator.paginate(Bucket=target_bucket, Prefix=normalized):
                    for obj in page.get("Contents") or []:
                        out.append(
                            {
                                "bucket": target_bucket,
                                "key": obj["Key"],
                                "size": obj.get("Size", 0),
                                "last_modified": obj.get("LastModified"),
                            }
                        )
                        if len(out) >= max_keys:
                            return out
                return out

            return await loop.run_in_executor(None, _list)
        except Exception as e:
            logger.error("Failed to list S3 objects under %s: %s", prefix, e)
            raise RuntimeError(f"S3 list failed: {e}") from e