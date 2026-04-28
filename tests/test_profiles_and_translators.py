from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.profiles import load_profile
from backend.providers.gemini_provider import GeminiProvider
from backend.providers.openai_compat_provider import OpenAICompatProvider
from backend.providers.tool_translator import to_openai
from backend.tools import SCHEMAS, run_tool


class DummyClient:
    pass


async def collect(gen):
    return [ev async for ev in gen]


def test_strauss_profile_loads_persona_and_kb_root():
    profile = load_profile("strauss")
    assert profile.id == "strauss"
    assert profile.kb_root.name == "kb"
    assert "advocate-in-residence" in profile.system_prompt
    assert "get_resume_summary" in profile.tools
    assert "Tell me about Shuttrr." in profile.suggestions


def test_unknown_explicit_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("definitely-not-a-profile")


def test_openai_tool_translation_wraps_shared_schema():
    tools = to_openai(SCHEMAS)
    first = tools[0]
    assert first["type"] == "function"
    assert first["function"]["name"] == SCHEMAS[0]["name"]
    assert first["function"]["parameters"] == SCHEMAS[0]["input_schema"]


def test_tool_allowlist_blocks_disabled_tool(kb_root):
    result = run_tool(
        "get_resume_summary",
        {},
        "toolu_disabled",
        root=kb_root,
        allowed_tools=("list_kb",),
    )
    assert result.is_error is True
    assert "not enabled" in result.content


def test_openai_compat_provider_uses_translated_tools_without_client_key():
    provider = OpenAICompatProvider(api_key_env="NO_SUCH_KEY", client=DummyClient())
    tools = provider.tools_for_provider(load_profile("strauss"))
    assert tools[0]["type"] == "function"


def test_gemini_provider_builds_function_declarations_without_client_key():
    provider = GeminiProvider(client=DummyClient())
    tools = provider.tools_for_provider(load_profile("strauss"))
    declarations = tools[0].function_declarations
    assert declarations[0].name == SCHEMAS[0]["name"]
    assert declarations[0].parameters_json_schema == SCHEMAS[0]["input_schema"]


class FakeAsyncStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.request = None

    async def create(self, **request):
        self.request = request
        return FakeAsyncStream(self.chunks)


class FakeOpenAIClient:
    def __init__(self, chunks):
        self.completions = FakeCompletions(chunks)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.asyncio
async def test_openai_provider_accumulates_streaming_tool_call():
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(content="I'll check.", tool_calls=None),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_resume_summary",
                                    arguments="",
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                type=None,
                                function=SimpleNamespace(name=None, arguments="{}"),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]
    client = FakeOpenAIClient(chunks)
    provider = OpenAICompatProvider(api_key_env="NO_SUCH_KEY", client=client)
    profile = load_profile("strauss")
    messages = [provider.format_user("resume?")]

    events = await collect(
        provider.stream(
            model="gpt-test",
            messages=messages,
            system=provider.system_for_provider(profile),
            tools=provider.tools_for_provider(profile),
            max_tokens=128,
        )
    )

    assert client.completions.request["stream"] is True
    assert messages[-1]["tool_calls"][0]["id"] == "call_1"
    assert any(e["type"] == "tool_use_start" for e in events)
    complete = [e for e in events if e["type"] == "tool_use_complete"][0]
    assert complete["tool_use_id"] == "call_1"
    assert complete["arguments"] == {}
    assert events[-1] == {"type": "message_done", "stop_reason": "tool_use"}
