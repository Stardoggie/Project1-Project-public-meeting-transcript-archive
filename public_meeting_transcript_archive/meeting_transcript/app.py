from flask import Flask
from pydantic import ValidationError
import os
from flask_migrate import Migrate
from meeting_transcript.responses import error_response,ApiError
from pydantic import ValidationError
from meeting_transcript.extensions import db
from meeting_transcript.health.routes import liveliness_bp
from meeting_transcript.governing_body.models_db import GoverningBody
from meeting_transcript.meetings.models_db import Meeting,Entities,KeyPhrases
from meeting_transcript.governing_body.routes import govbody_bp

API_PREFIX = "/api/v1/"
migrate = Migrate()



def create_app():
    '''uses factory pattern to create and return a new flask app'''
    app = Flask(__name__)
    app.register_blueprint(liveliness_bp,url_prefix="")
    app.register_blueprint(govbody_bp,url_prefix="/governing-bodies")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"] 
    db.init_app(app)#initilizing db with flask
    migrate.init_app(app,db)#helps manage connection to db
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_response(error.code,error.status,error.detail)
    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        first_error = error.errors()[0]
        detail_str = f"{first_error['loc']}: {first_error['msg']}"
        #UNPROCESSABLE_ENTITY -more specific than 400
        return error_response("validation_failed",422,detail_str)
    # @app.errorhandler(Exception)
    # def handle_Unhandled_Exception(error: Exception):
    #     return error_response("internal",500,"an unexpected error ocurred")
    #     #can handle status codes as well
    # @app.errorhandler(404)
    # def handle_resource_not_found(error):
    #     return error_response("not_found",404,"No route for given path!")
            
    return app