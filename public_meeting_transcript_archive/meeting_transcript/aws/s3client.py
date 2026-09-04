from meeting_transcript.aws.aws import get_s3_client







def upload_audio(bucket: str, key: str, content: bytes) -> None:
    """
        uploads audio to S3
    """
    client = get_s3_client()

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
    )


def get_object(bucket: str, key: str):
    """
        gets audio from s3
    """
    client = get_s3_client()

    return client.get_object(Bucket=bucket,Key=key)