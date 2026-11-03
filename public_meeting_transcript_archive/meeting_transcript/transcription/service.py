import json
import uuid
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.transcription.store import get_audio_key,store_transcription,store_job_key,get_job_key,analysis_check,revert_transcribe_failure
from meeting_transcript.aws.aws import get_transcribe_client
from meeting_transcript.aws.s3client import get_object
from meeting_transcript.analysis.service import comprehend_entities_and_phrases
ALLOWED_AUDIO_EXTENSIONS = {"mp3","wav"}
MAX_AUDIO_BYTES = 500 *1024*1024




def _existing_job_status(job_name:str)->str | None:
    """
        status of a transcribe job with this name, or None if there is no such job
    """
    client = get_transcribe_client()
    try:
        job = client.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
    except client.exceptions.BadRequestException as e:
        #transcribe reports a missing job as a bad request
        if "couldn't be found" in str(e):
            return None
        raise
    return job["TranscriptionJobStatus"]


def start_transcription_job(meeting_id:int)->dict:
    """
        tell transcribe to look for the meeting's audio in s3 to start the transcription job
        a failed job is deleted first so the same recording can be retried under the same name
    """
    #get audio key from db
    audio_key = get_audio_key(meeting_id=meeting_id)
    if not audio_key:
        return {"error":"no_recording","detail":"Attach a recording before starting transcription."}
    #get extension
    extension = audio_key.split(".",1)[-1].lower()
    #get job name
    job_name = audio_key.removeprefix("audio/").removesuffix(f".{extension}")
    job_name = job_name.split("/",1)[-1].lower()
    #job names are unique in transcribe, so check for an earlier attempt
    existing = _existing_job_status(job_name)
    if existing in ("QUEUED","IN_PROGRESS"):
        return {"job_name":job_name,"status":existing}
    if existing == "COMPLETED":
        return {"job_name":job_name,"status":"COMPLETED","detail":"Already transcribed; check the transcription status to store it."}
    if existing == "FAILED":
        get_transcribe_client().delete_transcription_job(TranscriptionJobName=job_name)
    #sending transcribe call
    get_transcribe_client().start_transcription_job(TranscriptionJobName=job_name,LanguageCode="en-US",MediaFormat=extension,Media={"MediaFileUri":f"s3://{BUCKET_NAME}/{audio_key}"},OutputBucketName=BUCKET_NAME,OutputKey=f"transcripts/{job_name}.json")
    #store job_name only after transcribe accepted the job, so a failed start leaves the meeting recorded
    store_job_key(meeting_id,job_name)
    return {"job_name":job_name,"status":"IN_PROGRESS"}


def get_transcription_job(meeting_id:int)->dict:
    """
        get an existing job out of AWS transcribe
    """
    job_name = get_job_key(meeting_id)
    if not job_name:
        return {"error":"not_started","detail":"Transcription hasn't been started for this meeting."}
    job = get_transcribe_client().get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
    status = job["TranscriptionJobStatus"]
    result = {"job_name":job_name,"status":status}
    if status == "COMPLETED":
        response = get_object(BUCKET_NAME,f"transcripts/{job_name}.json")
        payload = json.loads(response["Body"].read())
        result["transcript"] = payload["results"]["transcripts"][0]["transcript"]
        store_transcription(meeting_id,result["transcript"])
        #print(analysis_check(meeting_id))
        if analysis_check(meeting_id) is False:
            comprehend_entities_and_phrases(result["transcript"],meeting_id)

    elif status == "FAILED":
        result["failure_reason"] = job.get("FailureReason","unknown")
        revert_transcribe_failure(meeting_id)
    return result