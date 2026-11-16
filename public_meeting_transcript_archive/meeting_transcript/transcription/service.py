import json
import uuid
from meeting_transcript.config import BUCKET_NAME
from botocore.exceptions import ClientError
from meeting_transcript.transcription.store import get_audio_key,store_transcription,store_job_key,get_job_key,claim_analysis,release_analysis,revert_transcribe_failure,meeting_exists,meeting_not_found,already_transcribed
from meeting_transcript.aws.aws import get_transcribe_client,get_s3_client
from meeting_transcript.aws.s3client import get_object
from meeting_transcript.analysis.service import comprehend_entities_and_phrases
ALLOWED_AUDIO_EXTENSIONS = {"mp3","wav"}
MAX_AUDIO_BYTES = 500 *1024*1024
#s3 error codes for a missing object, head_object reports it as 404
MISSING_OBJECT_CODES = {"404","NoSuchKey","NotFound"}




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


def _transcript_exists(job_name:str)->bool:
    """
        true if transcribe's output for this job is still in s3
    """
    try:
        get_s3_client().head_object(Bucket=BUCKET_NAME,Key=f"transcripts/{job_name}.json")
    except ClientError as e:
        if e.response.get("Error",{}).get("Code") in MISSING_OBJECT_CODES:
            return False
        raise
    return True


def start_transcription_job(meeting_id:int)->dict:
    """
        tell transcribe to look for the meeting's audio in s3 to start the transcription job
        a failed job is deleted first so the same recording can be retried under the same name
    """
    if not meeting_exists(meeting_id):
        return meeting_not_found()
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
        #re-attaching audio clears the job name, so store it again for the status check
        store_job_key(meeting_id,job_name)
        return {"job_name":job_name,"status":existing}
    if existing == "COMPLETED":
        #transcript already stored from this job, so leave the archived meeting alone
        if already_transcribed(meeting_id,job_name):
            return {"job_name":job_name,"status":"COMPLETED","detail":"Already transcribed."}
        if _transcript_exists(job_name):
            #stays transcribing until the status check stores the transcript and archives it
            store_job_key(meeting_id,job_name)
            return {"job_name":job_name,"status":"COMPLETED","detail":"Already transcribed; check the transcription status to store it."}
        #the output is gone from s3, so delete the job and transcribe again
        existing = "FAILED"
    if existing == "FAILED":
        get_transcribe_client().delete_transcription_job(TranscriptionJobName=job_name)
    #sending transcribe call
    client = get_transcribe_client()
    try:
        client.start_transcription_job(TranscriptionJobName=job_name,LanguageCode="en-US",MediaFormat=extension,Media={"MediaFileUri":f"s3://{BUCKET_NAME}/{audio_key}"},OutputBucketName=BUCKET_NAME,OutputKey=f"transcripts/{job_name}.json")
    except client.exceptions.ConflictException:
        #another start made the job first, so report that job instead
        existing = _existing_job_status(job_name)
        if existing is None:
            raise
        store_job_key(meeting_id,job_name)
        return {"job_name":job_name,"status":existing}
    #store job_name only after transcribe accepted the job, so a failed start leaves the meeting recorded
    store_job_key(meeting_id,job_name)
    return {"job_name":job_name,"status":"IN_PROGRESS"}


def get_transcription_job(meeting_id:int)->dict:
    """
        get an existing job out of AWS transcribe
    """
    if not meeting_exists(meeting_id):
        return meeting_not_found()
    job_name = get_job_key(meeting_id)
    if not job_name:
        return {"error":"not_started","detail":"Transcription hasn't been started for this meeting."}
    job = get_transcribe_client().get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
    status = job["TranscriptionJobStatus"]
    result = {"job_name":job_name,"status":status}
    if status == "COMPLETED":
        try:
            response = get_object(BUCKET_NAME,f"transcripts/{job_name}.json")
        except ClientError as e:
            if e.response.get("Error",{}).get("Code") not in MISSING_OBJECT_CODES:
                raise
            #output is gone, back to recorded so starting again makes a new job
            revert_transcribe_failure(meeting_id)
            return {"job_name":job_name,"status":"FAILED","failure_reason":"The transcript output is missing from S3; start transcription again."}
        payload = json.loads(response["Body"].read())
        transcript = payload["results"]["transcripts"][0]["transcript"]
        #return what was stored, silent audio is stored as a placeholder message
        stored = store_transcription(meeting_id,transcript,job_name)
        #the meeting can be deleted while the transcript downloads
        if stored is None:
            return meeting_not_found()
        #or its audio replaced, and this transcript belongs to the old recording
        if stored is False:
            return {"job_name":job_name,"status":"SUPERSEDED","detail":"The meeting's recording was replaced while this transcript downloaded, so it was discarded. Start or check transcription for the new recording."}
        result["transcript"] = stored
        #only the caller that claims it analyses; comprehend runs unlocked and the results are only stored
        #if the meeting still has this job's transcript
        claimed = claim_analysis(meeting_id,job_name)
        if claimed is None:
            return meeting_not_found()
        if claimed == "superseded":
            return {"job_name":job_name,"status":"SUPERSEDED","detail":"The meeting's recording was replaced, so this transcript was discarded. Start or check transcription for the new recording."}
        if claimed == "running":
            #completed is only reported once the entities and key phrases are stored
            result["status"] = "ANALYZING"
            result["detail"] = "The transcript is stored; entity and key-phrase analysis is still running. Check again shortly."
        if claimed == "claimed":
            try:
                comprehend_entities_and_phrases(transcript,meeting_id,job_key=job_name)
            except Exception:
                #leave it unanalysed so the next check tries again
                release_analysis(meeting_id,job_name)
                raise

    elif status == "FAILED":
        result["failure_reason"] = job.get("FailureReason","unknown")
        revert_transcribe_failure(meeting_id)
    return result