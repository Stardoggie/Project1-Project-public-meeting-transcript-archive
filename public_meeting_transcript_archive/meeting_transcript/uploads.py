"""
    shared validation for multipart file uploads
"""
from meeting_transcript.responses import ApiError
from werkzeug.datastructures import FileStorage
#noted method that works for uploads
def read_upload(file_storage:FileStorage | None,allowed_extensions: set[str],max_bytes:int)->tuple[bytes,str]:
    """
        validate one uploaded file and return its bytes
    """

    #validate that we recieved a file
    if file_storage is None or not file_storage.filename:
        raise ApiError("validation_failed",422,"no file uploaded - multi-part/form-data expected")
    #validate the file extension
    extension = file_storage.filename.rsplit(".",1)[-1].lower() #-1 gives you the last value in an array
    if extension not in allowed_extensions:
        raise ApiError("unsupported_media_type",415,f"{extension} is not supported. Expected one of the following: {[e for e in allowed_extensions]}")
    #reads in all of the file (the bytes of it)
    content = file_storage.read()
    #validate that the file is within the allowed number of bytes
    if len(content)>max_bytes:
        raise ApiError("payload_too_large",413,f"file is {len(content)} bytes, max allowed is {max_bytes} bytes.")
    #validate the file actuall has data
    if not content:
        raise ApiError("validation_failed",422,"uploaded file is empty")
    return content, file_storage.filename