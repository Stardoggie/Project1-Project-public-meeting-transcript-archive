from meeting_transcript.aws.aws import get_s3_client


def get_object(bucket: str, key: str):
    """
        gets audio from s3
    """
    client = get_s3_client()

    return client.get_object(Bucket=bucket,Key=key)
