from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import json
import os
import boto3
from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()
# AgentCore Runtime and Lambda may set only AWS_DEFAULT_REGION, so fall back to it and then to boto3's own lookup
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or boto3.Session().region_name
BUCKET_NAME = os.environ["MEETING_BUCKET_NAME"]


def database_url() -> str | URL:
    """
        DATABASE_URL when set (local dev, tests); otherwise the deployed database, built from the RDS secret
        named by DATABASE_SECRET_ARN (the secret's ARN or name) so the password never sits in an environment variable
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    secret_arn = os.environ.get("DATABASE_SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("Set DATABASE_URL, or DATABASE_SECRET_ARN for the deployed database")
    secret = json.loads(
        boto3.client("secretsmanager", region_name=AWS_REGION).get_secret_value(SecretId=secret_arn)["SecretString"]
    )
    return URL.create(
        "postgresql+psycopg",
        username=secret["username"],
        password=secret["password"],
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        database=secret.get("dbname", "transcript_archive"),
        # sslmode=require encrypts the connection
        query={"sslmode": "require"},
    )
#app setting for logger
class AppSettings(BaseSettings) :
    """
    BaseSettings gives you the Pydantic checks, but for CONFIGURATION rather than data
    maps env rars to class properties.
        ex; SUPPORT_API_DATA_PATH -> data path
    env_file - the exact name of your .env file to look in
    env_prefix - the prefix for all the env vars to look for
    extra - decides how to contain env file vvars that dont have mappings in the class
    """
    model_config = SettingsConfigDict(env_file=[".env",".env.prod"], env_prefix="MEETING_", extra= "ignore")
    # giving default values means this class can still be instantiated withou an .env file
    data_path: Path = Path("data/logger.json")
    default_page_size: int = 5
    log_level: str = "DEBUG"

