# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
import asyncio
import copy
import io
import json
import string
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog

import src.ari_client as ari_module
import src.audio.audiosocket_server as socket_module
import src.engine as engine_module
from src.ari_client import ARIClient
from src.audio.audiosocket_server import AudioSocketServer
from src.engine import Engine
from src.logging_config import sanitize_secrets


def _check(condition, reason):
    # Failed privacy assertions must not reproduce the synthetic sensitive value.
    if not condition:
        raise AssertionError(reason)


class _LogSink:
    def __init__(self):
        self.records = []

    def _record(self, level, event, **fields):
        self.records.append(dict(level=level, event=event, **fields))

    def info(self, event, **fields):
        self._record('info', event, **fields)

    def warning(self, event, **fields):
        self._record('warning', event, **fields)

    def debug(self, event, **fields):
        self._record('debug', event, **fields)

    def error(self, event, **fields):
        self._record('error', event, **fields)


@pytest.fixture
def logs(monkeypatch):
    sink = _LogSink()
    for module in (engine_module, ari_module, socket_module):
        monkeypatch.setattr(module, 'logger', sink)
    return sink


def _engine():
    engine = Engine.__new__(Engine)
    engine._attended_transfer_agent_channel_to_call_id = {}
    engine._attended_transfer_dtmf_digits = {}
    engine._attended_transfer_dtmf_waiters = {}
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock())
    engine._save_session = AsyncMock()
    engine.conn_to_channel = {'fixture-connection': 'fixture-channel'}
    return engine


def _check_no_error_body(logs):
    for record in logs.records:
        _check('exc_info' not in record, 'DTMF log retained exception traceback')
        _check('error' not in record, 'DTMF log retained arbitrary exception body')


@pytest.mark.asyncio
async def test_unowned_pre_stasis_dtmf_never_logs_or_persists_digit(logs):
    engine = _engine()
    for digit in string.digits + '*#':
        event = {'channel': {'id': 'unowned-channel'}, 'digit': digit}
        before = copy.deepcopy(event)
        await engine._handle_dtmf_received(event)
        _check(event == before, 'redaction changed incoming DTMF event')
    _check(len(logs.records) == len(string.digits + '*#'), 'DTMF event logs lost')
    _check(all(r['event'] == 'Channel DTMF received' for r in logs.records), 'event name changed')
    _check(all('digit' not in r for r in logs.records), 'raw DTMF field logged')
    _check(not engine._attended_transfer_dtmf_digits, 'unowned DTMF retained')
    _check(engine.session_store.get_by_call_id.await_count == 0, 'unowned DTMF reached session store')
    _check(engine._save_session.await_count == 0, 'unowned DTMF reached session save')


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['aimee', 'aimee_main'])
async def test_owned_dtmf_keeps_first_decision_waiter_and_session(logs, context):
    engine = _engine()
    channel_id = 'owned-agent-channel'
    call_id = 'fixture-call'
    engine._attended_transfer_agent_channel_to_call_id[channel_id] = call_id
    session = SimpleNamespace(context_name=context, current_action={'type': 'attended_transfer'})
    engine.session_store.get_by_call_id.return_value = session
    waiter = asyncio.get_running_loop().create_future()
    engine._attended_transfer_dtmf_waiters[channel_id] = waiter
    first, later = string.digits[-1], string.digits[-2]
    await engine._handle_dtmf_received({'channel': {'id': channel_id}, 'digit': first})
    await engine._handle_dtmf_received({'channel': {'id': channel_id}, 'digit': later})
    _check(waiter.result() == first, 'native DTMF waiter result changed')
    _check(engine._attended_transfer_dtmf_digits[channel_id] == first, 'first decision overwritten')
    _check(session.current_action['decision_digit'] == first, 'session decision changed')
    _check(engine._save_session.await_count == 1, 'first-decision persistence changed')
    _check(all('digit' not in r for r in logs.records), 'owned DTMF field logged')


@pytest.mark.asyncio
async def test_persistence_error_is_classified_and_dtmf_waiter_still_settles(logs):
    engine = _engine()
    engine._attended_transfer_agent_channel_to_call_id['owned-agent-channel'] = 'fixture-call'
    engine.session_store.get_by_call_id.side_effect = RuntimeError(string.digits * 2)
    waiter = asyncio.get_running_loop().create_future()
    engine._attended_transfer_dtmf_waiters['owned-agent-channel'] = waiter
    digit = string.digits[-1]
    await engine._handle_dtmf_received({'channel': {'id': 'owned-agent-channel'}, 'digit': digit})
    _check(waiter.done() and waiter.result() == digit, 'persistence error broke DTMF waiter')
    _check_no_error_body(logs)
    _check(any(r.get('error_type') == 'RuntimeError' for r in logs.records), 'error classification missing')


class _SensitiveFailureMapping:
    def get(self, *_args):
        raise RuntimeError(string.digits * 2)


@pytest.mark.asyncio
async def test_ari_handler_error_has_no_payload_or_traceback(logs):
    await _engine()._handle_dtmf_received(_SensitiveFailureMapping())
    _check_no_error_body(logs)
    _check(logs.records[-1].get('error_type') == 'RuntimeError', 'ARI error classification missing')


@pytest.mark.asyncio
async def test_audiosocket_dtmf_log_has_no_digit_or_error_body(logs):
    engine = _engine()
    await engine._audiosocket_handle_dtmf('fixture-connection', string.digits[-1])
    _check('digit' not in logs.records[-1], 'AudioSocket DTMF field logged')
    engine.conn_to_channel = _SensitiveFailureMapping()
    await engine._audiosocket_handle_dtmf('fixture-connection', string.digits[-1])
    _check_no_error_body(logs)
    _check(logs.records[-1].get('error_type') == 'RuntimeError', 'AudioSocket classification missing')


@pytest.mark.asyncio
async def test_native_ari_dtmf_adapter_forwards_original_event_without_logging(logs):
    received = []

    async def handler(channel, digit):
        received.append((channel, digit))

    digit = string.digits[-1]
    await ARIClient.__new__(ARIClient).handle_dtmf_received(
        {'channel': {'id': 'fixture-channel'}, 'digit': digit}, handler
    )
    _check(received == [('fixture-channel', digit)], 'native DTMF adapter changed')
    _check(not logs.records, 'native DTMF adapter introduced logs')


@pytest.mark.asyncio
async def test_malformed_ari_json_omits_body_and_valid_dtmf_still_dispatches(logs):
    client = ARIClient.__new__(ARIClient)
    client._should_reconnect = True
    client._connected = True
    client.running = True
    received = []
    event = {'type': 'ChannelDtmfReceived', 'channel': {'id': 'fixture-channel'}, 'digit': string.digits * 2}

    async def handler(value):
        received.append(value)

    class _WebSocket:
        async def __aiter__(self):
            yield json.dumps(event)[:-1]
            yield json.dumps(event)
            await asyncio.sleep(0)
            client._should_reconnect = False

        async def close(self):
            pass

    client.websocket = _WebSocket()
    client.event_handlers = {'ChannelDtmfReceived': [handler]}
    await client._listen_with_reconnect()
    _check(received == [event], 'valid DTMF event no longer dispatches')
    warning = next(r for r in logs.records if r['event'] == 'Failed to decode ARI event JSON')
    _check('message' not in warning, 'malformed ARI log retained raw message')
    _check(warning.get('error_type') == 'JSONDecodeError', 'JSON error classification missing')


@pytest.mark.asyncio
async def test_ari_listener_failure_keeps_only_error_class_and_native_cleanup(logs):
    client = ARIClient.__new__(ARIClient)
    client._should_reconnect = True
    client._connected = True
    client.running = True
    client.event_handlers = {}
    closed = []

    class _FailingWebSocket:
        async def __aiter__(self):
            client._should_reconnect = False
            raise RuntimeError(string.digits * 2)
            yield  # Keep this an async iterator without invoking a transport.

        async def close(self):
            closed.append(True)

    client.websocket = _FailingWebSocket()
    await client._listen_with_reconnect()
    _check(closed == [True] and client.websocket is None, 'listener failure cleanup changed')
    _check(string.digits * 2 not in json.dumps(logs.records), 'listener error leaked DTMF content')
    _check(all('exc_info' not in r for r in logs.records), 'listener error retained traceback')


@pytest.mark.asyncio
async def test_audiosocket_callback_error_closes_and_omits_sensitive_body(logs):
    callback = AsyncMock(side_effect=RuntimeError(string.digits * 2))
    disconnected = AsyncMock()
    server = AudioSocketServer('::', 0, on_uuid=AsyncMock(return_value=True), on_audio=AsyncMock(),
                               on_dtmf=callback, on_disconnect=disconnected)
    reader = asyncio.StreamReader()
    token = uuid.uuid4().bytes
    payload = string.digits[-1].encode('ascii')
    reader.feed_data(bytes([socket_module.TYPE_UUID]) + len(token).to_bytes(2, 'big') + token)
    reader.feed_data(bytes([socket_module.TYPE_DTMF]) + len(payload).to_bytes(2, 'big') + payload)
    reader.feed_eof()
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    await server._connection_loop('fixture-connection', reader, writer)
    _check(callback.await_count == 1, 'AudioSocket DTMF callback changed')
    _check(disconnected.await_count == 1, 'AudioSocket disconnect cleanup changed')
    _check_no_error_body(logs)
    error = next(r for r in logs.records if r['event'] == 'AudioSocket connection error')
    _check(error.get('error_type') == 'RuntimeError', 'callback error classification missing')


@pytest.mark.asyncio
async def test_audiosocket_decode_error_is_classified_without_payload(logs):
    callback = AsyncMock()
    disconnected = AsyncMock()
    server = AudioSocketServer('::', 0, on_uuid=AsyncMock(return_value=True), on_audio=AsyncMock(),
                               on_dtmf=callback, on_disconnect=disconnected)

    class _SensitiveBytes(bytes):
        def decode(self, *_args, **_kwargs):
            raise RuntimeError(string.digits * 2)

    token = uuid.uuid4().bytes
    payload = _SensitiveBytes(string.digits.encode('ascii'))
    frames = iter([bytes([socket_module.TYPE_UUID]) + len(token).to_bytes(2, 'big'), token,
                   bytes([socket_module.TYPE_DTMF]) + len(payload).to_bytes(2, 'big'), payload])

    class _Reader:
        async def readexactly(self, _size):
            frame = next(frames, None)
            if frame is None:
                raise asyncio.IncompleteReadError(b'', _size)
            return frame

    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    await server._connection_loop('fixture-connection', _Reader(), writer)
    _check(callback.await_count == 0, 'failed decode unexpectedly reached DTMF callback')
    _check(disconnected.await_count == 1, 'decode failure cleanup changed')
    _check_no_error_body(logs)
    error = next(r for r in logs.records if r['event'] == 'Failed to decode DTMF digit')
    _check(error.get('error_type') == 'RuntimeError', 'decode error classification missing')


@pytest.mark.parametrize('key', ['digit', 'digits', 'dtmf', 'DTMF_Digit', 'dtmf-digits', 'consent_dtmf', 'decision_digit'])
@pytest.mark.parametrize('kind', ['text', 'numeric', 'empty', 'null', 'boolean', 'mapping'])
def test_dtmf_keys_always_redact_entire_value_without_prefix(key, kind):
    values = {'text': string.digits * 2, 'numeric': ord(string.digits[-1]), 'empty': '',
              'null': None, 'boolean': False, 'mapping': {'contents': string.digits * 2}}
    event = {'event': 'fixture', key: values[kind], 'duration_ms': 120}
    before = copy.deepcopy(event)
    sanitized = sanitize_secrets(None, 'info', event)
    _check(sanitized[key] == '***REDACTED***', 'DTMF value was only partially redacted')
    _check(event == before, 'redaction changed caller-owned structured data')
    _check(sanitized['duration_ms'] == 120, 'non-sensitive context changed')


@pytest.mark.parametrize('renderer', ['json', 'console'])
def test_real_structlog_renderer_redacts_nested_dtmf_without_mutating_payload(renderer):
    sensitive = string.digits * 2
    payload = {'Digit': sensitive, 'events': [[{'Decision_Digit': sensitive}]]}
    before = copy.deepcopy(payload)
    stream = io.StringIO()
    render = structlog.processors.JSONRenderer() if renderer == 'json' else structlog.dev.ConsoleRenderer(colors=False)
    log = structlog.wrap_logger(structlog.PrintLogger(stream), processors=[sanitize_secrets, render])
    log.info('Channel DTMF received', digit=sensitive, payload=payload)
    output = stream.getvalue()
    _check(sensitive not in output, 'rendered log leaked DTMF payload')
    _check(sensitive[:2] + '***REDACTED***' not in output, 'rendered log leaked DTMF prefix')
    _check(payload == before, 'rendering changed processing payload')


def test_unrelated_existing_secret_and_context_redaction_remains_unchanged():
    event = {'event': 'fixture', 'duration_ms': 120, 'api_key': 'fixture-key', 'passthrough': True}
    sanitized = sanitize_secrets(None, 'info', event)
    _check(sanitized['api_key'] == 'fi***REDACTED***', 'unrelated secret policy changed')
    _check(sanitized['duration_ms'] == 120 and sanitized['passthrough'], 'unrelated log context changed')
