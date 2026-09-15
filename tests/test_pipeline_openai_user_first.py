import copy

import pytest

from src.config import OpenAIProviderConfig
from src.pipelines.openai import OpenAILLMAdapter


def adapter(options=None):
    return OpenAILLMAdapter("local", None, OpenAIProviderConfig(api_key="test"), options)


def confirmation_history():
    return [
        {"role": "assistant", "content": "Hi, this is Aimee. How can I help?"},
        {"role": "user", "content": "I would like to leave a message."},
        {"role": "assistant", "content": "Who is the message for?"},
        {"role": "user", "content": "Gary"},
        {"role": "assistant", "content": "What would you like me to tell Gary?"},
        {"role": "user", "content": "The sky is blue."},
        {"role": "assistant", "content": "I have: The sky is blue. Is that right?"},
    ]


def test_receptionist_confirmation_keeps_all_words_without_mutating_call_history():
    llm = adapter({"user_first_history": True})
    history = confirmation_history()
    original = copy.deepcopy(history)
    messages = llm._coalesce_messages("Yes.", {"prior_messages": history}, llm._compose_options({}))
    assert [m["role"] for m in messages] == ["user", "assistant"] * 4 + ["user"]
    assert messages[0] == {"role": "user", "content": ""}
    assert messages[1:-1] == original
    assert messages[-1] == {"role": "user", "content": "Yes."}
    assert history == original


@pytest.mark.parametrize("enabled", [None, False, "true", "false"])
def test_default_and_non_boolean_options_leave_crustacea_history_unchanged(enabled):
    llm = adapter({"user_first_history": enabled})
    messages = confirmation_history()
    assert llm._coalesce_messages("ignored", {"messages": messages}, llm._compose_options({})) is messages


def test_explicit_runtime_false_overrides_pipeline_setting():
    llm = adapter({"user_first_history": True})
    assert llm._compose_options({"user_first_history": False})["user_first_history"] is False


@pytest.mark.parametrize("prefix", [[], [{"role": "system", "content": "Receptionist"}]])
def test_opening_is_wire_only_idempotent_and_keeps_tool_correlations(prefix):
    tool_call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "deposit", "arguments": "{}"}}]}
    result = {"role": "tool", "tool_call_id": "call_1", "content": "confirmed"}
    messages = prefix + confirmation_history() + [{"role": "user", "content": "Yes."}, tool_call, result]
    original = copy.deepcopy(messages)
    llm = adapter({"user_first_history": True})
    options = llm._compose_options({})
    wire = llm._coalesce_messages("", {"messages": messages}, options)
    assert wire[-2] is tool_call
    assert wire[-1] is result
    assert messages == original
    assert llm._coalesce_messages("", {"messages": wire}, options) is wire


def test_does_not_drop_or_rewrite_interrupted_speech():
    messages = [
        {"role": "assistant", "content": "Hi, this is Aimee."},
        {"role": "user", "content": "Tomorrow morning"},
        {"role": "assistant", "content": "What time"},
        {"role": "user", "content": "I was not finished, at eleven."},
    ]
    llm = adapter({"user_first_history": True})
    assert llm._coalesce_messages("", {"messages": messages}, llm._compose_options({}))[1:] == messages


@pytest.mark.parametrize("messages", [[], [{"role": "system", "content": "Context"}],
    [{"role": "user", "content": "Hello"}]])
def test_other_histories_do_not_gain_empty_turns(messages):
    llm = adapter({"user_first_history": True})
    assert llm._coalesce_messages("", {"messages": messages}, llm._compose_options({})) == messages
