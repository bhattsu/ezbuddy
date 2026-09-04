import boto3
from botocore.config import Config
from app.config.settings import settings
import logging

logger = logging.getLogger(__name__)

# Configure boto3 with timeouts and retries
# Document generation can take a long time for multi-page PDFs in one VLM call
_boto_read_timeout = max(1800, int(getattr(settings, "DOC_GEN_TIMEOUT", 1800) or 1800) + 120)
boto_config = Config(
    read_timeout=_boto_read_timeout,
    connect_timeout=30,
    retries={
        'max_attempts': 3,
        'mode': 'adaptive'
    }
)

class AWSClients:
    """Singleton AWS client manager"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_clients()
        return cls._instance

    def _initialize_clients(self):
        """Initialize all AWS clients"""
        try:
            self.textract = boto3.client(
                "textract",
                region_name=settings.AWS_REGION,
                verify=settings.AWS_VERIFY_SSL,
                # aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                # aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=boto_config
            )

            self.bedrock = boto3.client(
                "bedrock-runtime",
                region_name=settings.AWS_REGION,
                verify=settings.AWS_VERIFY_SSL,
                # aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                # aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=boto_config
            )

            self.s3 = boto3.client(
                "s3",
                region_name=settings.AWS_REGION,
                verify=settings.AWS_VERIFY_SSL,
                # aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                # aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=boto_config
            )

            logger.info("AWS clients initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {str(e)}")
            raise

    def get_textract(self):
        return self.textract

    def get_bedrock(self):
        return self.bedrock

    def get_s3(self):
        return self.s3

# Global instance
aws_clients = AWSClients()