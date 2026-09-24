import anthropic.types as at
import google.genai.types as gt
import openai.types.chat as oc
from openai.types.responses import Response

from stardust_sdk.extract import detect_provider, extract, from_anthropic, from_gemini, from_openai


def test_anthropic_counts_cache_reads_and_writes_as_input():
    msg = at.Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 50},
    })
    u = from_anthropic(msg)
    assert (u.provider, u.model, u.tokens_in, u.tokens_out, u.tokens_cached_in) == ("anthropic", "claude-sonnet-5", 450, 20, 300)
    assert detect_provider(msg) == "anthropic"


def test_openai_chat_completion():
    resp = oc.ChatCompletion.model_validate({
        "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-4o-2024-08-06",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050, "prompt_tokens_details": {"cached_tokens": 600}},
    })
    u = from_openai(resp)
    assert (u.model, u.tokens_in, u.tokens_out, u.tokens_cached_in) == ("gpt-4o-2024-08-06", 1000, 50, 600)
    assert detect_provider(resp) == "openai"


def test_openai_responses_api():
    resp = Response.model_construct(
        id="r1", object="response", model="gpt-5",
        usage={"input_tokens": 200, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 80,
               "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 280},
    )
    u = from_openai(resp)
    assert (u.tokens_in, u.tokens_out, u.tokens_cached_in) == (200, 80, None)


def test_gemini_adds_thinking_to_output():
    resp = gt.GenerateContentResponse(
        model_version="gemini-2.5-pro",
        usage_metadata=gt.GenerateContentResponseUsageMetadata(
            prompt_token_count=500, candidates_token_count=100, thoughts_token_count=250, cached_content_token_count=200,
        ),
    )
    u = from_gemini(resp)
    assert (u.provider, u.model, u.tokens_in, u.tokens_out, u.tokens_cached_in) == ("google", "gemini-2.5-pro", 500, 350, 200)
    assert detect_provider(resp) == "google"


def test_plain_dicts_from_http_apis():
    assert extract({"type": "message", "model": "claude-haiku-4-5", "usage": {"input_tokens": 5, "output_tokens": 7}}).tokens_out == 7
    assert extract({"object": "chat.completion", "model": "gpt-4o", "usage": {"prompt_tokens": 3, "completion_tokens": 4}}).tokens_in == 3
    g = extract({"modelVersion": "gemini-2.5-flash", "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2}})
    assert (g.provider, g.tokens_in, g.tokens_out) == ("google", 9, 2)


def test_missing_usage_and_unknown_shapes():
    assert from_anthropic({"model": "x"}) is None
    assert extract({"hello": "world"}) is None
    assert extract(object()) is None
    # Model falls back to the requested one when the response omits it.
    assert from_gemini({"usageMetadata": {"promptTokenCount": 1}}, model="gemini-2.5-pro").model == "gemini-2.5-pro"
