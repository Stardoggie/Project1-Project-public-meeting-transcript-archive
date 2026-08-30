

#base image is some existing image in docker hub that you will build on top of
# - want bse image to be smoll, so they dont include a bunch of unneeded things
FROM python:3.14-slim
#create a directory inside the container for our code to live in
WORKDIR /app
# copy code from project (local machine) ot the container working directory
COPY . .

#instaall all needed needed directories into container
#run lets you run any kind of cli command
RUN pip install --no-cache-dir .

#use ENV to set enviroment variables
#FLASK_APP can be used instead of having to put --app on command when running flask app
ENV FLASK_APP=meeting_transcript.app

#telling Docker to keep a port open in the container to allow traffic through
EXPOSE 5000

#run app
#upgrade db
#then run flask app in the container on port 5000
CMD ["sh","-c","flask db upgrade && flask run --host=0.0.0.0 --port=5000"]