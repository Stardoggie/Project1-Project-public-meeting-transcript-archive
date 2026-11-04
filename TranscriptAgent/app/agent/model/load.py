from langchain_aws import ChatBedrockConverse

# The organization's SCP blocks global. inference profiles; us. ones are allowed
MODEL_ID = "us.anthropic.claude-sonnet-5-5"


def load_model() -> ChatBedrockConverse:
    """Get Bedrock model client using IAM credentials."""
    return ChatBedrockConverse(model=MODEL_ID)
