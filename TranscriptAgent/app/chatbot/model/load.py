import logging
import os
import re
from langchain_aws import ChatBedrockConverse
# The organization's SCP blocks global. inference profiles; us. ones are allowed
MODEL_ID = "us.anthropic.claude-sonnet-5-5"
# Room for long answers; full transcripts go straight to the user via show_full_transcript instead
MAX_TOKENS = 8192
# A guardrail's id, or its ARN
GUARDRAIL_IDENTIFIER = re.compile(r"[a-z0-9]+|arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{12}:guardrail/[a-z0-9]+")
log = logging.getLogger(__name__)


def guardrail_config() -> dict | None:
    """The Bedrock Guardrail from GUARDRAIL_ID and GUARDRAIL_VERSION, or None when neither is set (local dev).

    Settings that are only half there or look wrong stop the chatbot from starting, so it never runs unguarded by
    mistake. The version must be a published number: DRAFT can change under the running chatbot.
    """
    guardrail_id = os.getenv("GUARDRAIL_ID", "").strip()
    version = os.getenv("GUARDRAIL_VERSION", "").strip()
    if not guardrail_id and not version:
        log.warning("GUARDRAIL_ID and GUARDRAIL_VERSION are not set; the chatbot runs without a guardrail")
        return None
    if not guardrail_id or not version:
        raise RuntimeError("Set both GUARDRAIL_ID and GUARDRAIL_VERSION, or neither")
    if not GUARDRAIL_IDENTIFIER.fullmatch(guardrail_id):
        raise RuntimeError(f"GUARDRAIL_ID {guardrail_id!r} isn't a guardrail id or ARN")
    if not version.isdigit():
        raise RuntimeError(f"GUARDRAIL_VERSION must be a published version number, not {version!r}")
    # trace stays off: it would put the text that tripped a filter back into the response
    return {"guardrailIdentifier": guardrail_id, "guardrailVersion": version, "trace": "disabled"}


def load_model() -> ChatBedrockConverse:
    """Get Bedrock model client using IAM credentials.

    With a guardrail, guard_last_turn_only has it check the visitor's newest message and every reply, but not tool
    results: transcripts are long (each check is billed by length) and often discuss crime or name people, which
    the filters would flag.
    """
    guardrail = guardrail_config()
    return ChatBedrockConverse(model=MODEL_ID, max_tokens=MAX_TOKENS, guardrail_config=guardrail,
                               guard_last_turn_only=guardrail is not None)
