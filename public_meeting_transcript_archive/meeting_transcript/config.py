from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from urllib.parse import quote
import json
import os
import boto3
from dotenv import load_dotenv
load_dotenv()
AWS_REGION = os.environ["AWS_REGION"]
BUCKET_NAME = os.environ["MEETING_BUCKET_NAME"]
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


def database_url() -> str:
    """
        the database to connect to: DATABASE_URL when it is set (local .env, tests),
        otherwise built from the secrets manager secret named in DATABASE_SECRET_ID (the deployed agent),
        so the deployed password only ever lives in secrets manager
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    secret_id = os.environ.get("DATABASE_SECRET_ID")
    if not secret_id:
        raise RuntimeError("Set DATABASE_URL, or DATABASE_SECRET_ID to the name of the database's secret")
    #read the host, port, user, password and database name stored in the secret
    secrets = boto3.client("secretsmanager",region_name=AWS_REGION)
    secret = json.loads(secrets.get_secret_value(SecretId=secret_id)["SecretString"])
    user = quote(secret["username"],safe="")
    password = quote(secret["password"],safe="")#escapes any character that would break the url
    address = f"{secret['host']}:{secret.get('port',5432)}/{secret.get('dbname','transcript_archive')}"
    #sslmode=require encrypts the connection
    return f"postgresql+psycopg://{user}:{password}@{address}?sslmode=require"
