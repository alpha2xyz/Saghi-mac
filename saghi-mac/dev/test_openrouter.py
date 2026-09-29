#!/usr/bin/env python3
"""
Phase 6 unit tests for saghi/openrouter.py -- NO live network calls
anywhere in this file (no real OpenRouter API key is available for testing
in this environment; a real key exists nowhere to authenticate against). Two
groups:

  1. A REAL macOS Keychain round-trip (save/load/delete) -- genuinely
     exercises `keyring`'s macOS backend, no mocking. Uses
     SAGHI_KEYCHAIN_SERVICE (this module's own override, mirroring
     paths.py's SAGHI_DATA_DIR convention) pointed at a disposable service
     name, so this test run can NEVER touch the real "Saghi" Keychain item
     a real user might have saved -- SAGHI_DATA_DIR does not provide this
     isolation on its own, since the Keychain is a separate store from
     settings.json.

  2. rephrase()/test_connection() request-building + response-parsing,
     against a fake httpx transport (httpx.MockTransport) injected via the
     `client=` parameter -- covers the success path, every classified
     failure mode (timeout, connection error, each of several non-200
     statuses, non-JSON body, JSON with the wrong shape in three different
     ways, non-string content, missing key, missing model), and confirms
     TypeError is raised (not swallowed) for genuinely wrong-typed
     arguments. `api_key=` is always passed explicitly in this group too,
     so none of it touches the real Keychain either.

Run:
    PYTHONPATH=. <venv>/bin/python dev/test_openrouter.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Isolate this test run's Keychain writes from the real "Saghi" service --
# MUST be set before saghi.openrouter is imported would be nice, but the
# service name is read lazily (_keychain_service() is called fresh on every
# save/load/delete call, not cached at import time), so setting it here
# before first use is sufficient.
os.environ["SAGHI_KEYCHAIN_SERVICE"] = "Saghi-test-openrouter"

import httpx  # noqa: E402

from saghi import openrouter  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


def _transport(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion_response(content: str = "rephrased text") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ============================================================================
# Group 1: REAL Keychain round-trip
# ============================================================================

print("--- Real macOS Keychain round-trip (SAGHI_KEYCHAIN_SERVICE=Saghi-test-openrouter) ---")

check(openrouter.load_api_key() is None, "no key stored yet under the test service (clean starting state)")

openrouter.save_api_key("sk-or-test-xxxx")
check(openrouter.load_api_key() == "sk-or-test-xxxx", "load_api_key() returns exactly what was saved")

openrouter.save_api_key("sk-or-test-yyyy")
check(openrouter.load_api_key() == "sk-or-test-yyyy", "a second save overwrites the first, not appends/duplicates")

openrouter.delete_api_key()
check(openrouter.load_api_key() is None, "load_api_key() returns None after delete")

# Deleting again (nothing stored) must be a silent no-op, not an error.
openrouter.delete_api_key()
check(True, "delete_api_key() on an already-empty slot does not raise")

try:
    openrouter.save_api_key("")
    check(False, "save_api_key('') should have raised ValueError")
except ValueError:
    check(True, "save_api_key('') raises ValueError (genuine bad-arg, not silently absorbed)")

print("\nKeychain probe result: REAL round-trip (save -> load -> overwrite -> delete -> load=None) "
      "all worked genuinely in this sandboxed process, no permission error, no mocking.")

# ============================================================================
# Group 2: rephrase() / test_connection() -- fake transport, no network
# ============================================================================

print("\n--- rephrase(): success path ---")

client = _transport(lambda req: _completion_response("النص المعاد صياغته"))
result = openrouter.rephrase(
    "النص الأصلي", "google/gemini-3.1-flash-lite", "أعد الصياغة بإيجاز",
    client=client, api_key="sk-or-fake",
)
check(result.ok is True, "success path returns ok=True")
check(result.text == "النص المعاد صياغته", f"success path returns the rephrased text (got {result.text!r})")
check(result.error is None, "success path has no error")

print("\n--- rephrase(): request shape sent to OpenRouter ---")

captured = {}


def _capture_handler(req: httpx.Request) -> httpx.Response:
    captured["url"] = str(req.url)
    captured["auth"] = req.headers.get("authorization")
    import json as _json

    captured["body"] = _json.loads(req.content)
    return _completion_response("ok")


client = _transport(_capture_handler)
openrouter.rephrase("hello", "some/model", "be brief", client=client, api_key="sk-or-abc123")
check(captured["url"] == "https://openrouter.ai/api/v1/chat/completions", "posts to the documented OpenRouter endpoint")
check(captured["auth"] == "Bearer sk-or-abc123", "sends the key as a Bearer token")
check(captured["body"]["model"] == "some/model", "request body carries the model id")
check(
    captured["body"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ],
    "request body carries system=instructions, user=text",
)

print("\n--- rephrase(): timeout ---")

client = _transport(lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k", timeout_s=5.0)
check(result.ok is False, "timeout returns ok=False")
check(result.text == "original", "timeout preserves the original text unchanged")
check("timed out" in (result.error or ""), f"timeout error mentions timing out (got {result.error!r})")

print("\n--- rephrase(): connection/network error (not a timeout) ---")

client = _transport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("connection refused")))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "connection error returns ok=False")
check(result.text == "original", "connection error preserves the original text")
check("network error" in (result.error or ""), f"connection error is classified as a network error (got {result.error!r})")
check("timed out" not in (result.error or ""), "connection error is NOT misclassified as a timeout")

print("\n--- rephrase(): non-200 statuses ---")

for status in (401, 429, 500):
    client = _transport(lambda req, s=status: httpx.Response(s, text=f'{{"error":"boom {s}"}}'))
    result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
    check(result.ok is False, f"HTTP {status} returns ok=False")
    check(result.text == "original", f"HTTP {status} preserves the original text")
    check(f"HTTP {status}" in (result.error or ""), f"HTTP {status} error names the status code (got {result.error!r})")
    check(f"boom {status}" in (result.error or ""), f"HTTP {status} error includes a body snippet (got {result.error!r})")

print("\n--- rephrase(): malformed response bodies ---")

client = _transport(lambda req: httpx.Response(200, text="not json at all"))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "non-JSON 200 body returns ok=False")
check(result.text == "original", "non-JSON 200 body preserves the original text")
check("invalid JSON" in (result.error or ""), f"non-JSON body error says invalid JSON (got {result.error!r})")

client = _transport(lambda req: httpx.Response(200, json={"unexpected": "shape"}))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "JSON with no 'choices' key returns ok=False")
check(result.text == "original", "missing-'choices' body preserves the original text")
check("unexpected response shape" in (result.error or ""), f"missing-'choices' error names the shape problem (got {result.error!r})")

client = _transport(lambda req: httpx.Response(200, json={"choices": []}))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "empty 'choices' list returns ok=False (IndexError path)")
check(result.text == "original", "empty-'choices' body preserves the original text")

client = _transport(lambda req: httpx.Response(200, json={"choices": [{"message": {}}]}))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "'message' with no 'content' key returns ok=False (KeyError path)")
check(result.text == "original", "missing-'content' body preserves the original text")

client = _transport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": 12345}}]}))
result = openrouter.rephrase("original", "m", "i", client=client, api_key="k")
check(result.ok is False, "non-string 'content' returns ok=False")
check(result.text == "original", "non-string 'content' preserves the original text")
check("content is not text" in (result.error or ""), f"non-string content error is specific (got {result.error!r})")

print("\n--- rephrase(): missing key / missing model (no network call made) ---")

network_called = {"n": 0}


def _fail_if_called(req: httpx.Request) -> httpx.Response:
    network_called["n"] += 1
    return _completion_response("should not happen")


client = _transport(_fail_if_called)
result = openrouter.rephrase("original", "m", "i", client=client, api_key=None)
check(result.ok is False, "no api_key (and nothing in the real Keychain under the default service) returns ok=False")
check(result.text == "original", "missing-key case preserves the original text")
check("API key" in (result.error or ""), f"missing-key error mentions the key (got {result.error!r})")
check(network_called["n"] == 0, "missing-key case never made a network call")

client = _transport(_fail_if_called)
result = openrouter.rephrase("original", "", "i", client=client, api_key="k")
check(result.ok is False, "blank model returns ok=False")
check(result.text == "original", "missing-model case preserves the original text")
check("model" in (result.error or ""), f"missing-model error mentions the model (got {result.error!r})")
check(network_called["n"] == 0, "missing-model case never made a network call")

print("\n--- rephrase(): blank instructions is a VALID call (not short-circuited) ---")

client = _transport(lambda req: _completion_response("fine"))
result = openrouter.rephrase("original", "m", "", client=client, api_key="k")
check(result.ok is True, "blank instructions still makes the call and can succeed")
check(result.text == "fine", "blank instructions doesn't change response handling")

print("\n--- rephrase(): TypeError for genuinely wrong-typed arguments (never silently absorbed) ---")

for bad_call, desc in [
    (lambda: openrouter.rephrase(None, "m", "i"), "text=None"),
    (lambda: openrouter.rephrase("t", 123, "i"), "model=123"),
    (lambda: openrouter.rephrase("t", "m", None), "instructions=None"),
]:
    try:
        bad_call()
        check(False, f"{desc} should have raised TypeError")
    except TypeError:
        check(True, f"{desc} raises TypeError instead of being silently absorbed")

print("\n--- test_connection(): success and failure, same never-raises contract ---")

client = _transport(lambda req: _completion_response("OK"))
result = openrouter.test_connection("google/gemini-3.1-flash-lite", client=client, api_key="k")
check(result.ok is True, "test_connection() success path returns ok=True")
check(result.text == "OK", "test_connection() success path returns the model's reply text")

client = _transport(lambda req: httpx.Response(401, text='{"error":"invalid key"}'))
result = openrouter.test_connection("google/gemini-3.1-flash-lite", client=client, api_key="bad-key")
check(result.ok is False, "test_connection() failure path returns ok=False")
check("HTTP 401" in (result.error or ""), f"test_connection() surfaces the real status (got {result.error!r})")
check(result.text == "ping", "test_connection() failure preserves its own fixed probe text (never raises)")

print(f"\nALL PASSED ({_checks} checks)")
