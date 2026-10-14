"""Optional Azure OpenAI wrapper. The whole system also runs without it."""
from openai import AzureOpenAI

from . import config

_client = None


def enabled() -> bool:
    return bool(config.AOAI_ENDPOINT and config.AOAI_KEY)


def chat(messages: list[dict], json_mode: bool = False) -> tuple[str, int, int]:
    global _client
    if _client is None:
        _client = AzureOpenAI(azure_endpoint=config.AOAI_ENDPOINT, api_key=config.AOAI_KEY,
                              api_version=config.AOAI_VERSION)
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = _client.chat.completions.create(model=config.AOAI_DEPLOYMENT, messages=messages, temperature=0, **kwargs)
    u = r.usage
    return (r.choices[0].message.content or "").strip(), u.prompt_tokens, u.completion_tokens
