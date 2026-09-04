from flask import Flask,json,request,g
from pydantic import ValidationError
from flask_migrate import Migrate
from meeting_transcript.responses import error_response,ApiError
from pydantic import ValidationError
from meeting_transcript.extensions import db
from meeting_transcript.health.routes import liveliness_bp
from meeting_transcript.governing_body.routes import govbody_bp
from meeting_transcript.meetings.routes import meetings_bp
from meeting_transcript.transcription.routes import transcribe_bp
from meeting_transcript.config import AppSettings
from meeting_transcript.logging import log
from botocore.exceptions import BotoCoreError,ClientError
from meeting_transcript.analysis.routes import analysis_bp
import logging
import time
import uuid
import os


API_PREFIX = "/api/v1/governing-bodies"
migrate = Migrate()



class JsonFormatter(logging.Formatter):
    """
        a format for log calls using the format function
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add structured fields supplied through `extra`
        for field in (
            "method","path","status_code","duration_ms","correlation_id",
        ):
            if hasattr(record, field):
                log_entry[field] = getattr(record, field)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

_CLIENT_FAULT_STATUS = {
    "AccessDeniedException": 403,
    "AccessDenied": 403,
    "UnrecognizedClientException": 403,
    "ValidationException": 422,
    "InvalidParameterException": 422,
    "InvalidParameterValueException": 422,
    "TextSizeLimitExceededException": 422,
    "InvalidRequestException": 422,
    "UnsupportedLanguagePairException": 422,
    "ThrottlingException": 429,
    "TooManyRequestsException": 429,
    "ResourceNotFoundException": 404,
}

def configure_logging() -> None:
    """
        sets up the config for logging
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    app_logger = logging.getLogger("meeting_transcript")
    app_logger.setLevel(AppSettings().log_level)
    app_logger.handlers.clear()
    app_logger.addHandler(handler)
    app_logger.propagate = False

def create_app():
    '''uses factory pattern to create and return a new flask app'''
    configure_logging()

    logger = logging.getLogger("meeting_transcript")
    app = Flask(__name__)
    app.logger.info(f"Public Meeting Transcript Archive Flask APP started")
    app.register_blueprint(liveliness_bp,url_prefix="")
    app.register_blueprint(govbody_bp,url_prefix=API_PREFIX)
    app.register_blueprint(meetings_bp,url_prefix=API_PREFIX)
    app.register_blueprint(transcribe_bp,url_prefix=API_PREFIX)
    app.register_blueprint(analysis_bp,url_prefix=API_PREFIX)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"] 
    db.init_app(app)#initilizing db with flask
    migrate.init_app(app,db)#helps manage connection to db
    #handles problems in this api
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_response(error.code,error.status,error.detail)
    #handles validation errors
    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        first_error = error.errors()[0]
        detail_str = f"{first_error['loc']}: {first_error['msg']}"
        #UNPROCESSABLE_ENTITY -more specific than 400
        app.logger.exception(f"validation_failed {detail_str}")
        return error_response("validation_failed",422,detail_str)
    #handles unknown errors
    @app.errorhandler(Exception)
    def handle_Unhandled_Exception(error: Exception):
        app.logger.exception("unhandled Exception",error)
        return error_response("internal",500,"an unexpected error ocurred")
        #can handle status codes as well
    #handles not found errors
    @app.errorhandler(404)
    def handle_resource_not_found(error):
        return error_response("not_found",404,"No route for given path!")
    #handls value errors for datetime
    @app.errorhandler(ValueError)
    def handle_value_not_correct(error :ValueError):
        app.logger.exception(f"ValueError: {error}")
        return error_response("unprocessable_entity",422,"time not in date format.")
    #handles boto3 client side errors
    @app.errorhandler(ClientError)
    def handle_aws_client_error(error):
        aws_code = error.response.get("Error",{}).get("Code","UnknownAwsError")
        status = _CLIENT_FAULT_STATUS.get(aws_code,502)#default to a 502 error -bad gateway
        app.logger.exception("AWS call failed : %s",aws_code)
        return error_response("aws_error",status,aws_code)
    #handles serverside errors for AWS
    @app.errorhandler(BotoCoreError)
    def handle_botocore_error(error):
        app.logger.exception("AWS SDK/configuration error")
        return error_response("aws_configuration_error",500,type(error).__name__)
    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        return error_response("method_not_allowed",405,"method not allowed!")
    @app.errorhandler(AttributeError)
    def handle_not_found(error):
            return error_response("not_found",404,"transcript not found!")
    @app.before_request
    def start_request_logging():
        g.request_start_time = time.perf_counter()

        # Reuse a supplied correlation ID, or create one
        g.correlation_id = request.headers.get(
            "X-Correlation-ID",
            uuid.uuid4().hex,
        )
    @app.after_request
    def log_request(response):
        duration_ms = round(
            (time.perf_counter() - g.request_start_time) * 1000,
            2,
        )
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                 "duration_ms": duration_ms,
                "correlation_id": g.correlation_id
            },
        )
        return response

            
    return app