"""Retry on DROPPED CONNECTIONS only (for example ConnectionResetError, the failure that stopped the rivaroxaban 2018Q4 run on a PubMed call).
At most 3 attempts with a short backoff (1 s, then 2 s). Nothing else is retried: HTTP errors, bad data and every other exception are raised
at once. If the last attempt still fails, the original error is raised and the run stops: a lookup is never skipped silently, and the same
request (same query, same date limit) is repeated unchanged.
Added 2026-10-03 after the rivaroxaban 2017Q4 run (documented in eval_runs.md); it does not change any request, prompt or rule."""
import time

import requests

ATTEMPTS = 3
BACKOFF = (1.0, 2.0)  # seconds to wait after the 1st and the 2nd failed attempt

RETRYABLE = (ConnectionError,  # the builtin family: ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ConnectionRefusedError
             requests.exceptions.ConnectionError,  # requests wraps a reset or refused connection in this (it is not a builtin ConnectionError)
             requests.exceptions.ChunkedEncodingError)  # the connection broke in the middle of a response
try:
    import anthropic
    RETRYABLE += (anthropic.APIConnectionError,)  # the Anthropic SDK's own dropped-connection error
except ImportError:  # the network helper must work without the SDK
    pass


def _say(message):
    print(message, flush=True)


def retry_connection(fn, *args, what="network call", log=_say, **kwargs):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as e:
            if attempt == ATTEMPTS:
                raise
            wait = BACKOFF[attempt - 1]
            log(f"  {what}: {type(e).__name__} on attempt {attempt} of {ATTEMPTS}; retrying in {wait:.0f}s")
            time.sleep(wait)
