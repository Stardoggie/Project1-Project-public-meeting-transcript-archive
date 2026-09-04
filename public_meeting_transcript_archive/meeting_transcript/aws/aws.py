import boto3
from functools import lru_cache
from meeting_transcript.config import AWS_REGION

@lru_cache(maxsize=1) #caching only one return value from this function
def get_session()->boto3.Session:
    """
        ONE SHARED session for the entire app
    """
    return boto3.Session(region_name=AWS_REGION)


#having a cache without a max size can still be helpful to retain results
#staying here until get specific made
@lru_cache(maxsize=None)
def get_client(service_name:str):
    """
        return a boto3 client
    """
    return get_session().client(service_name)

#get transcribe
@lru_cache(maxsize=None)
def get_s3_client():
    """
        gets the s3 client for use in s3client.py
    """
    return get_session().client("s3")
#get s3
@lru_cache(maxsize=None)
def get_transcribe_client():
    """
        gets transcribe client for use in transcription service.py
    """
    return get_session().client("transcribe")
#get comprehend
@lru_cache(maxsize=None)
def get_comprehend_client():
    """
        get comprehend client for analysis
    """
    return get_session().client("comprehend")