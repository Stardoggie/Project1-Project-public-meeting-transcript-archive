from langchain_aws import ChatBedrockConverse

# The organization's SCP blocks global. inference profiles; us. ones are allowed
MODEL_ID = "us.anthropic.claude-sonnet-5-5"
# Room for long answers; full transcripts go straight to the user via show_full_transcript instead
MAX_TOKENS = 8192


def load_model() -> ChatBedrockConverse:
    """Get Bedrock model client using IAM credentials."""
    return ChatBedrockConverse(model=MODEL_ID, max_tokens=MAX_TOKENS)
