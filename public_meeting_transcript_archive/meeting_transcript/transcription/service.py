import json
import uuid
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.transcription.store import get_audio_key,store_transcription,store_job_key,get_job_key,analysis_check,revert_transcribe_failure
from meeting_transcript.aws.aws import get_transcribe_client
from meeting_transcript.aws.s3client import get_object
from meeting_transcript.analysis.service import comprehend_entities_and_phrases
ALLOWED_AUDIO_EXTENSIONS = {"mp3","wav"}
MAX_AUDIO_BYTES = 500 *1024*1024




def start_transcription_job(meeting_id:int)->dict:
    """
        store the given autio is s3 and then tell transcribe to look for it to start the transcription job
    """
    #get audio key from db
    audio_key = get_audio_key(meeting_id=meeting_id)
    #get extension
    extension = audio_key.split(".",1)[-1].lower()
    #get job name
    job_name = audio_key.removeprefix("audio/").removesuffix(f".{extension}")
    job_name = job_name.split("/",1)[-1].lower()
    #store job_name
    store_job_key(meeting_id,job_name)
    #sending transcribe call
    get_transcribe_client().start_transcription_job(TranscriptionJobName=job_name,LanguageCode="en-US",MediaFormat=extension,Media={"MediaFileUri":f"s3://{BUCKET_NAME}/{audio_key}"},OutputBucketName=BUCKET_NAME,OutputKey=f"transcripts/{job_name}.json")
    return {"job_name":job_name,"status":"IN_PROGRESS"}


def get_transcription_job(meeting_id:int)->dict:
    """
        get an existing job out of AWS transcribe
    """
    job_name = get_job_key(meeting_id)
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