"""
Optional OpenRouter post-transcription rephrase (README_AR.md's «مساعد
OpenRouter الاختياري» subsection, nested under «## الاستخدام السريع» --
i.e. documented as part of the live-DICTATION flow, not the separate
«## تفريغ ملف وCaptions» file-transcription section, which is why
file-job integration was skipped, not just implementation convenience).

Behavior contract (all six numbered points from README_AR.md's own
description):
  1. Optional, off by default (`Settings.openrouter_enabled`).
  2. User supplies a free-form model id string, e.g.
     "google/gemini-3.1-flash-lite" -- any id valid on their account, never
     validated against a fixed list here.
  3. The API key is a SECRET: stored in the macOS Keychain via `keyring`,
     NEVER in settings.json (the Keychain is the macOS secret store). Model id and free-form instructions are NOT secrets -- they
     live in `Settings.openrouter_model` / `Settings.openrouter_instructions`
     instead (see settings.py).
  4. Audio never leaves the machine -- only the already-transcribed TEXT is
     sent to OpenRouter. Nothing in this module ever touches audio.
  5. `test_connection()` backs the settings page's «اختبار الاتصال» button.
  6. `rephrase()` NEVER raises for an expected failure mode (missing key,
     no model configured, network/timeout error, non-200 status, malformed
     response) -- every one of those returns `RephraseResult(ok=False,
     text=<the original input text, unchanged>, error=<message>)`, so a
     caller can always just use `.text` and get a safe result, matching the
     README's explicit "إذا تعذرت إعادة الصياغة، يلصق صاغي النص المحلي بدل
     فقدانه" (if rephrasing fails, paste the local text instead of losing
     it) promise. It DOES raise for genuine programming errors (wrong
     argument types) -- those are bugs to fix, not runtime conditions to
     absorb.

No Qt import anywhere in this module -- same zero-Qt convention as
settings.py/engine.py/recorder.py/hotkey.py/paste.py, so it's usable (and
unit-testable) from a plain script with no QApplication, and from
dictation.py's background QThread without any GUI-thread concern.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx
import keyring
import keyring.errors

logger = logging.getLogger("saghi.openrouter")

# Keychain identity. Service name overridable via SAGHI_KEYCHAIN_SERVICE --
# mirrors paths.py's SAGHI_DATA_DIR override convention exactly, so tests
# (and dev/grab_screens.py) can point at a throwaway Keychain service
# instead of the real "Saghi" one used by the actual app. Without this, a
# test run could silently overwrite or delete a real user's stored key --
# SAGHI_DATA_DIR does NOT provide this isolation, since the Keychain is a
# separate store from the data dir's settings.json.
_KEYCHAIN_SERVICE_ENV = "SAGHI_KEYCHAIN_SERVICE"
_DEFAULT_KEYCHAIN_SERVICE = "Saghi"
_KEYCHAIN_USERNAME = "openrouter-api-key"

_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"

# How much of a non-200 response body to fold into the error message --
# enough to be genuinely diagnosable (an OpenRouter error body is usually a
# short JSON object), not so much that a pathological body turns the
# settings page's error line into a wall of text.
_BODY_SNIPPET_MAX_CHARS = 300

_DEFAULT_TIMEOUT_S = 15.0


def _keychain_service() -> str:
    return os.environ.get(_KEYCHAIN_SERVICE_ENV) or _DEFAULT_KEYCHAIN_SERVICE


# ---- Keychain storage (key only -- model id / instructions are NOT secret,
# see settings.py) ------------------------------------------------------


def save_api_key(key: str) -> None:
    """Store the OpenRouter API key in the macOS Keychain. Never touches settings.json."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("API key must be a non-empty string")
    keyring.set_password(_keychain_service(), _KEYCHAIN_USERNAME, key)


def load_api_key() -> Optional[str]:
    """Read the stored key, or None if nothing is saved. Never raises."""
    try:
        return keyring.get_password(_keychain_service(), _KEYCHAIN_USERNAME)
    except keyring.errors.KeyringError:
        logger.exception("Keychain read failed for the OpenRouter API key")
        return None


def delete_api_key() -> None:
    """Remove the stored key. A no-op (not an error) if nothing was stored."""
    try:
        keyring.delete_password(_keychain_service(), _KEYCHAIN_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # nothing stored -- deleting a non-existent key is a no-op
    except keyring.errors.KeyringError:
        logger.exception("Keychain delete failed for the OpenRouter API key")


# ---- rephrase call ------------------------------------------------------


@dataclass
class RephraseResult:
    ok: bool
    # On success: the rephrased text. On failure: the ORIGINAL input text,
    # byte-for-byte unchanged -- callers can always paste/save `.text`
    # without checking `.ok` first and still get a safe, correct result.
    text: str
    error: Optional[str] = None


def rephrase(
    text: str,
    model: str,
    instructions: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: Optional[httpx.Client] = None,
    api_key: Optional[str] = None,
) -> RephraseResult:
    """
    Send `text` to OpenRouter's chat completions endpoint with `instructions`
    as the system prompt, and return the rephrased result.

    `client`: inject an httpx.Client (e.g. one built with a
    httpx.MockTransport) for tests -- when omitted, a real client is
    constructed and closed for this one call. Never touches the network in
    a test that always passes `client`.

    `api_key`: inject the key directly (tests) instead of reading the real
    Keychain -- when omitted (the real call path, e.g. dictation.py),
    `load_api_key()` is used. Passing an explicit key here, even an empty
    one, bypasses the Keychain entirely.

    NEVER raises for an expected failure mode -- see module docstring. DOES
    raise TypeError for wrong-typed arguments (a caller bug, not a runtime
    condition).
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    if not isinstance(model, str):
        raise TypeError(f"model must be a str, got {type(model).__name__}")
    if not isinstance(instructions, str):
        raise TypeError(f"instructions must be a str, got {type(instructions).__name__}")

    resolved_key = api_key if api_key is not None else load_api_key()
    if not resolved_key:
        return RephraseResult(ok=False, text=text, error="no OpenRouter API key configured")
    if not model.strip():
        return RephraseResult(ok=False, text=text, error="no model id configured")

    # A blank instructions string is a legitimate (if unhelpful) user
    # choice -- OpenRouter just sees an empty system message. Not
    # short-circuited here, unlike the missing-key/missing-model cases,
    # which are genuine configuration problems this app can detect locally
    # without a network round trip.
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": text},
        ],
    }
    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http_client = client if client is not None else httpx.Client()
    try:
        try:
            # timeout= passed at the REQUEST level, not just relying on the
            # client's own default -- an injected client (built by a test
            # around a MockTransport) may have no timeout configured on
            # construction at all, so this is the only place the timeout
            # value is guaranteed to actually apply.
            response = http_client.post(
                _CHAT_COMPLETIONS_URL, json=payload, headers=headers, timeout=timeout_s
            )
        except httpx.TimeoutException:
            # Must be caught BEFORE httpx.RequestError below --
            # TimeoutException is a subclass of RequestError, so the
            # broader except would otherwise shadow this one and every
            # timeout would misreport as a generic "network error".
            return RephraseResult(ok=False, text=text, error=f"timed out after {timeout_s:.0f}s")
        except httpx.RequestError as exc:
            return RephraseResult(ok=False, text=text, error=f"network error: {exc}")

        if response.status_code != 200:
            snippet = response.text[:_BODY_SNIPPET_MAX_CHARS]
            return RephraseResult(ok=False, text=text, error=f"HTTP {response.status_code}: {snippet}")

        try:
            data = response.json()
        except ValueError:
            return RephraseResult(ok=False, text=text, error="malformed response: invalid JSON")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return RephraseResult(
                ok=False, text=text, error="malformed response: unexpected response shape"
            )

        if not isinstance(content, str):
            return RephraseResult(ok=False, text=text, error="malformed response: content is not text")

        return RephraseResult(ok=True, text=content, error=None)
    finally:
        if owns_client:
            http_client.close()


# A small, fixed, real-shaped call -- deliberately not a no-op ping to some
# other OpenRouter endpoint, so a successful test genuinely exercises the
# same request/response path rephrase() itself uses (auth header, model id,
# JSON parsing), matching README_AR.md's «اختبار الاتصال» button.
_TEST_CONNECTION_TEXT = "ping"
_TEST_CONNECTION_INSTRUCTIONS = "This is a connection test. Reply with a single word: OK."


def test_connection(
    model: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: Optional[httpx.Client] = None,
    api_key: Optional[str] = None,
) -> RephraseResult:
    """Minimal real-shaped call backing the settings page's «اختبار الاتصال» button. Never raises for expected failures -- see rephrase()."""
    return rephrase(
        _TEST_CONNECTION_TEXT,
        model,
        _TEST_CONNECTION_INSTRUCTIONS,
        timeout_s=timeout_s,
        client=client,
        api_key=api_key,
    )
