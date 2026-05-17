"""Tools to generate from OpenAI prompts.
Adopted from https://github.com/zeno-ml/zeno-build/"""

import asyncio
import logging
import os
import random
import time
from typing import Any

import aiolimiter
import openai
from openai import AsyncOpenAI, OpenAI

client: OpenAI | None = None
aclient: AsyncOpenAI | None = None
from tqdm.asyncio import tqdm_asyncio

# /stress A1.18 P1-2 (2026-05-16): lock-protect lazy init + env-hash check.
# Pre-fix: concurrent threads + changed OPENAI_BASE_URL could mix old/new
# clients (last-writer-wins, local caller could hold different client than
# the module global). Now: lock guards mutation, env-fingerprint guards
# stale-env (api_key + base_url change forces reinit).
import hashlib
import threading
_clients_lock = threading.Lock()
_clients_env_fingerprint: str | None = None


def _env_fingerprint(api_key: str, base_url: str | None) -> str:
    h = hashlib.sha256()
    h.update((api_key or "").encode("utf-8"))
    h.update(b"|")
    h.update((base_url or "").encode("utf-8"))
    return h.hexdigest()


def _require_openai_clients() -> tuple[OpenAI, AsyncOpenAI]:
    global client, aclient, _clients_env_fingerprint
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable must be set when using OpenAI API."
        )
    base_url = os.environ.get("OPENAI_BASE_URL")
    fp = _env_fingerprint(api_key, base_url)
    with _clients_lock:
        if client is None or aclient is None or _clients_env_fingerprint != fp:
            client = OpenAI(api_key=api_key, base_url=base_url)
            aclient = AsyncOpenAI(api_key=api_key, base_url=base_url)
            _clients_env_fingerprint = fp
        return client, aclient


def retry_with_exponential_backoff(  # type: ignore
    func,
    initial_delay: float = 1,
    exponential_base: float = 2,
    jitter: bool = True,
    max_retries: int = 3,
    # /stress A1.18-re (B-585 P1-6-B codex, 2026-05-17): drop BadRequestError
    # from retryable — it is almost always non-transient (invalid payload,
    # context-window overflow, unsupported model). Retrying it delays the run
    # AND destroys forensic signal needed to distinguish model-context overflow
    # from endpoint outage. Keep RateLimit + InternalServerError (transient).
    errors: tuple[Any] = (
        openai.RateLimitError,
        openai.InternalServerError,
    ),
):
    """Retry a function with exponential backoff."""

    def wrapper(*args, **kwargs):  # type: ignore
        # Initialize variables
        num_retries = 0
        delay = initial_delay
        last_error: Exception | None = None

        # Loop until a successful response or max_retries is hit or an exception is raised
        while True:
            try:

                return func(*args, **kwargs)

            # Retry on specified errors
            except errors as e:
                # Increment retries
                num_retries += 1
                last_error = e

                # Check if max retries has been reached
                if num_retries > max_retries:
                    # /stress A1.18-re B-585: preserve original cause chain so
                    # forensic info (status code, request_id, error type) is
                    # available to the caller. Pre-fix raised a bare
                    # Exception("Maximum number of retries (N) exceeded") with
                    # no traceback link to the underlying API error.
                    raise RuntimeError(
                        f"Maximum number of retries ({max_retries}) exceeded "
                        f"(last error: {type(e).__name__}: {e})"
                    ) from e

                # Increment the delay
                delay *= exponential_base * (1 + jitter * random.random())

                # Sleep for the delay
                time.sleep(delay)

            # Raise exceptions for any errors not specified
            except Exception as e:
                raise e

    return wrapper


async def _throttled_openai_completion_acreate(
    engine: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    limiter: aiolimiter.AsyncLimiter,
) -> str:
    """Return the completion text directly.

    /stress A1.18 P1-3 (2026-05-16): pre-fix returned the raw SDK response
    object on success but a chat-shaped dict (`{"choices": [{"message":
    {"content": ""}}]}`) on fallback, while the caller indexed as
    `x["choices"][0]["text"]`. Both branches now return a plain string.
    """
    _, local_aclient = _require_openai_clients()
    last_error: Exception | None = None
    async with limiter:
        for _ in range(3):
            try:
                resp = await local_aclient.completions.create(
                    engine=engine,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                )
                return resp.choices[0].text
            except openai.RateLimitError as e:
                logging.warning(
                    "OpenAI API rate limit exceeded. Sleeping for 10 seconds."
                )
                last_error = e
                await asyncio.sleep(10)
            except openai.APIError as e:
                logging.warning(f"OpenAI API error: {e}")
                last_error = e
                break
        # /stress A1.18-re (B-584 P1-5-B codex, 2026-05-17): fail-loud instead
        # of silent empty-string return. Pre-fix returned "" after rate-limit
        # exhaustion or APIError → indistinguishable from legitimate empty model
        # output → evaluator/judge/agent path consumed it as measured model
        # behavior, corrupting SR + error taxonomy. Per user direction
        # 2026-05-17 Q2=A: surface infrastructure failure rather than mask it.
        raise RuntimeError(
            f"OpenAI completion API failed after 3 attempts "
            f"(last error: {type(last_error).__name__ if last_error else 'unknown'}: {last_error})"
        ) from last_error


async def agenerate_from_openai_completion(
    prompts: list[str],
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    requests_per_minute: int = 300,
) -> list[str]:
    """Generate from OpenAI Completion API.

    Args:
        prompts: list of prompts
        temperature: Temperature to use.
        max_tokens: Maximum number of tokens to generate.
        top_p: Top p to use.
        context_length: Length of context to use.
        requests_per_minute: Number of requests per minute to allow.

    Returns:
        List of generated responses.
    """
    _require_openai_clients()

    limiter = aiolimiter.AsyncLimiter(requests_per_minute)
    async_responses = [
        _throttled_openai_completion_acreate(
            engine=engine,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            limiter=limiter,
        )
        for prompt in prompts
    ]
    responses = await tqdm_asyncio.gather(*async_responses)
    # /stress A1.18 P1-3 (2026-05-16): throttler now returns str directly.
    return list(responses)


@retry_with_exponential_backoff
def generate_from_openai_completion(
    prompt: str,
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    local_client, _ = _require_openai_clients()
    response = local_client.completions.create(
        prompt=prompt,
        engine=engine,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        stop=[stop_token],
    )
    # /stress A1.18 P1-3 (2026-05-16): SDK returns object, not dict.
    answer: str = response.choices[0].text
    return answer


async def _throttled_openai_chat_completion_acreate(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    top_p: float,
    limiter: aiolimiter.AsyncLimiter,
) -> str:
    """Return the chat-completion text directly.

    /stress A1.18 P1-3 (2026-05-16): pre-fix returned the raw SDK response on
    success and a dict on fallback; caller indexed both as dicts. Both branches
    now return a plain string.
    """
    _, local_aclient = _require_openai_clients()
    last_error: Exception | None = None
    async with limiter:
        for _ in range(3):
            try:
                resp = await local_aclient.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                )
                return resp.choices[0].message.content or ""
            except openai.RateLimitError as e:
                logging.warning(
                    "OpenAI API rate limit exceeded. Sleeping for 10 seconds."
                )
                last_error = e
                await asyncio.sleep(10)
            except asyncio.exceptions.TimeoutError as e:
                logging.warning("OpenAI API timeout. Sleeping for 10 seconds.")
                last_error = e
                await asyncio.sleep(10)
            except openai.APIError as e:
                logging.warning(f"OpenAI API error: {e}")
                last_error = e
                break
        # /stress A1.18-re (B-584 sibling P1-5-B codex, 2026-05-17): fail-loud
        # — see _throttled_openai_completion_acreate above for rationale.
        raise RuntimeError(
            f"OpenAI chat completion API failed after 3 attempts "
            f"(last error: {type(last_error).__name__ if last_error else 'unknown'}: {last_error})"
        ) from last_error


async def agenerate_from_openai_chat_completion(
    messages_list: list[list[dict[str, str]]],
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    requests_per_minute: int = 300,
) -> list[str]:
    """Generate from OpenAI Chat Completion API.

    Args:
        messages_list: list of message list
        temperature: Temperature to use.
        max_tokens: Maximum number of tokens to generate.
        top_p: Top p to use.
        context_length: Length of context to use.
        requests_per_minute: Number of requests per minute to allow.

    Returns:
        List of generated responses.
    """
    _require_openai_clients()

    limiter = aiolimiter.AsyncLimiter(requests_per_minute)
    async_responses = [
        _throttled_openai_chat_completion_acreate(
            model=engine,
            messages=message,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            limiter=limiter,
        )
        for message in messages_list
    ]
    responses = await tqdm_asyncio.gather(*async_responses)
    # /stress A1.18 P1-3 (2026-05-16): throttler now returns str directly.
    return list(responses)


@retry_with_exponential_backoff
def generate_from_openai_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    local_client, _ = _require_openai_clients()
    response = local_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    answer: str = response.choices[0].message.content
    return answer


@retry_with_exponential_backoff
# debug only
def fake_generate_from_openai_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    if "OPENAI_API_KEY" not in os.environ:
        raise ValueError(
            "OPENAI_API_KEY environment variable must be set when using OpenAI API."
        )

    answer = "Let's think step-by-step. This page shows a list of links and buttons. There is a search box with the label 'Search query'. I will click on the search box to type the query. So the action I will perform is \"click [60]\"."
    return answer
