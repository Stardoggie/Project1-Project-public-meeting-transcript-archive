# Web front end

One page, `index.html`, where the public can ask about governing bodies, meetings and transcripts, and read or
download full transcripts. It talks only to the read-only `public_agent` runtime (`app/agent/public_main.py`), which
can't add, change or delete anything. Clerks manage meetings and recordings with the terminal chat (`../cli/`).
It runs on this computer for now; how to host it for the public is still to be decided.

## Local
Start the public agent in `TranscriptAgent/` (port 8090 keeps it apart from the clerks' agent on 8080):
```
agentcore dev --agent public_agent --port 8090
```
then, in this folder:
```
python server.py
```
Then open http://127.0.0.1:8501. The server only listens on this computer. To use the deployed `public_agent` with
your own AWS credentials instead, run `uv run --project ../app/agent python server.py --runtime-arn <its ARN>`
(`agentcore status` shows it).
