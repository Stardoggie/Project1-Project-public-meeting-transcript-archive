def parse_payload(payload: dict) -> tuple[str, str]:
    """The prompt string and actor id from a request; the conversation history itself is kept per session."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    actor_id = payload.get("actorId", "default")
    if not isinstance(actor_id, str):
        raise ValueError("actorId must be a string")
    if not actor_id:
        actor_id = "default"
    if "messages" in payload and "prompt" not in payload:
        raise ValueError("send a 'prompt' string; the conversation history is kept per session")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt, actor_id
