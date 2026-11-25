"""Privacy-safe Prometheus exposition for process-local stella metrics."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any



_COUNTERS = (
    ("stella_webhook_events", "webhook_events", "Webhook events accepted by this process."),
    ("stella_webhook_dispatch_completed", "webhook_dispatch_completed", "Webhook events dispatched successfully."),
    ("stella_webhook_dispatch_failures", "webhook_dispatch_failures", "Webhook event dispatch failures."),
    ("stella_webhook_dispatch_queue_waits", "webhook_dispatch_queue_waits", "Events that waited for per-room dispatch ordering."),
    ("stella_text_messages_received", "text_messages_received", "Text messages received by this process."),
    ("stella_commands_received", "commands_received", "Commands received by this process."),
    ("stella_ai_requests", "ai_requests", "AI requests handled by this process."),
    ("stella_ai_assist_latency_seconds", "ai_assist_latency_seconds_total", "Cumulative end-to-end AI assist processing time."),
    ("stella_ai_assist_latency_observations", "ai_assist_latency_observations", "Completed AI assist latency observations."),
    ("stella_ai_provider_queue_wait_observations", "ai_provider_queue_wait_observations", "AI provider queue wait observations."),
    ("stella_ai_errors", "ai_errors", "AI provider errors and fallbacks recorded by this process."),
    ("stella_ai_cache_hits", "ai_cache_hits", "Short-lived safe AI answer cache hits."),
    ("stella_ai_cache_misses", "ai_cache_misses", "New eligible AI cache requests."),
    ("stella_ai_singleflight_waits", "ai_singleflight_waits", "Concurrent requests sharing an in-flight AI call."),
    ("stella_ai_shared_cache_hits", "ai_shared_cache_hits", "Cross-user hits for generic, non-personal short-lived answers."),
    ("stella_ai_shared_cache_misses", "ai_shared_cache_misses", "Generic answer cache misses."),
    ("stella_ai_answer_schema_requests", "ai_answer_schema_requests", "Requests using the minimal answer-only schema."),
    ("stella_ai_output_budgeted_requests", "ai_output_budgeted_requests", "Requests with a dynamic output token budget."),
    ("stella_ai_output_unbounded_requests", "ai_output_unbounded_requests", "Complex requests intentionally left without an output token cap."),
    ("stella_ai_tool_context_compactions", "ai_tool_context_compactions", "Tool results compacted only for temporary follow-up context."),
    ("stella_messages_sent", "messages_sent", "Messages sent by this process."),
    ("stella_api_requests", "api_requests", "Anarchy API requests made by this process."),
    ("stella_api_errors", "api_errors", "Anarchy API request errors recorded by this process."),
    ("stella_dj_tracks_started", "dj_tracks_started", "DJ tracks started by this process."),
    ("stella_dj_playback_errors", "dj_playback_errors", "DJ playback errors recorded by this process."),
    ("stella_dj_download_cache_hits", "dj_download_cache_hits", "DJ audio downloads reused from the short-lived disk cache."),
    ("stella_dj_download_cache_misses", "dj_download_cache_misses", "DJ audio downloads fetched because no reusable cached file was available."),
    ("stella_dj_download_cache_evictions", "dj_download_cache_evictions", "DJ audio files removed from the bounded temporary cache."),
    ("stella_dj_audio_output_unavailable", "dj_audio_output_unavailable", "DJ playback stopped after its LiveKit audio output became unavailable."),
    ("stella_dj_redecode_avoided", "dj_redecode_avoided", "Redundant full-track decodes avoided after terminal audio output failures."),
    ("stella_message_send_retries", "message_send_retries", "Rate-limited outgoing message retries."),
    ("stella_message_send_429", "message_send_429", "Outgoing message rate-limit responses."),
    ("stella_message_send_failures", "message_send_failures", "Outgoing message blocks that could not be delivered."),
    ("stella_message_send_recovered", "message_send_recovered", "Outgoing message blocks recovered from a definitive rate limit."),
    ("stella_message_send_pending_queued", "message_send_pending_queued", "Outgoing message blocks temporarily queued in process memory."),
    ("stella_message_send_pending_expired", "message_send_pending_expired", "Pending message blocks that expired before delivery."),
    ("stella_message_send_pending_abandoned", "message_send_pending_abandoned", "Pending message blocks abandoned after a non-rate-limit error."),
)


def _finite_number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, number) if math.isfinite(number) else 0.0


def render_prometheus_metrics(
    counters: Mapping[str, Any],
    source_health: Mapping[str, Any] | None = None,
    *,
    uptime_seconds: float = 0.0,
    memory_bytes: float | None = None,
    cpu_percent: float | None = None,
    provider_usage: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[bytes, str]:
    """Render aggregate-only metrics; never attach user, room or message labels."""
    # Prometheus is only needed for explicit /metrics scrapes. Keep it out of
    # the normal bot import path so a cold start loads fewer third-party modules.
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        generate_latest,
    )

    registry = CollectorRegistry()
    for metric_name, key, description in _COUNTERS:
        metric = Counter(metric_name, description, registry=registry)
        metric.inc(_finite_number(counters.get(key, 0)))

    external_requests = 0.0
    external_failures = 0.0
    for item in (source_health or {}).values():
        if not isinstance(item, Mapping):
            continue
        external_requests += _finite_number(item.get("requests", 0))
        external_failures += _finite_number(item.get("failures", 0))

    for metric_name, key, description in (
        ("stella_ai_provider_requests", "ai_provider_requests", "AI provider requests made by this process."),
        ("stella_ai_provider_429", "ai_provider_429", "AI provider rate-limit responses."),
        ("stella_ai_provider_retries", "ai_provider_retries", "AI provider retries after rate limits."),
        ("stella_ai_provider_daily_quota_hits", "ai_provider_daily_quota_hits", "AI daily quota exhaustion events."),
        ("stella_ai_provider_input_tokens", "ai_provider_input_tokens", "Input tokens reported by the AI provider."),
        ("stella_ai_provider_output_tokens", "ai_provider_output_tokens", "Output tokens reported by the AI provider."),
        ("stella_ai_provider_responses_completed", "ai_provider_responses_completed", "Structured AI responses completed."),
        ("stella_ai_provider_token_usage_responses", "ai_provider_token_usage_responses", "Successful AI responses with token usage reported by the provider."),
        ("stella_ai_provider_failovers", "ai_provider_failovers", "AI requests completed through a backup provider."),
        ("stella_ai_provider_primary_failures", "ai_provider_primary_failures", "AI provider failures before fallback."),
        ("stella_ai_provider_circuit_opened", "ai_provider_circuit_opened", "Times an AI provider circuit breaker opened."),
        ("stella_ai_provider_circuit_skips", "ai_provider_circuit_skips", "Provider calls skipped while unavailable."),
        ("stella_ai_user_rate_limited", "ai_user_rate_limited", "AI requests rejected by per-user rate limits."),
    ):
        Counter(metric_name, description, registry=registry).inc(
            _finite_number(counters.get(key, 0))
        )

    provider_input_tokens = Counter(
        "stella_ai_provider_model_input_tokens",
        "Input tokens reported for each provider and model.",
        ["provider", "model"],
        registry=registry,
    )
    provider_output_tokens = Counter(
        "stella_ai_provider_model_output_tokens",
        "Output tokens reported for each provider and model.",
        ["provider", "model"],
        registry=registry,
    )
    provider_responses = Counter(
        "stella_ai_provider_model_responses",
        "Successful AI responses reported for each provider and model.",
        ["provider", "model"],
        registry=registry,
    )
    for item in list(provider_usage or [])[:12]:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "unknown")[:48]
        model = str(item.get("model") or "unknown")[:96]
        provider_input_tokens.labels(provider, model).inc(_finite_number(item.get("input_tokens")))
        provider_output_tokens.labels(provider, model).inc(_finite_number(item.get("output_tokens")))
        provider_responses.labels(provider, model).inc(_finite_number(item.get("responses")))

    Counter(
        "stella_webhook_dispatch_queue_wait_seconds",
        "Cumulative seconds spent waiting for per-room webhook dispatch.",
        registry=registry,
    ).inc(_finite_number(counters.get("webhook_dispatch_queue_wait_seconds", 0)))
    Counter(
        "stella_webhook_dispatch_processing_seconds",
        "Cumulative seconds spent dispatching webhook events.",
        registry=registry,
    ).inc(_finite_number(counters.get("webhook_dispatch_processing_seconds", 0)))

    Counter(
        "stella_ai_output_budget_tokens",
        "Sum of configured dynamic output token budgets for bounded requests.",
        registry=registry,
    ).inc(_finite_number(counters.get("ai_output_budget_tokens", 0)))
    Counter(
        "stella_ai_tool_context_chars_original",
        "Characters received from tools before temporary context compaction.",
        registry=registry,
    ).inc(_finite_number(counters.get("ai_tool_context_chars_original", 0)))
    Counter(
        "stella_ai_tool_context_chars_stored",
        "Characters retained from tool results in temporary context.",
        registry=registry,
    ).inc(_finite_number(counters.get("ai_tool_context_chars_stored", 0)))

    Gauge(
        "stella_ai_provider_queue_wait_seconds",
        "Cumulative seconds AI requests waited for provider capacity.",
        registry=registry,
    ).set(_finite_number(counters.get("ai_provider_queue_wait_seconds", 0)))
    Counter(
        "stella_ai_provider_latency_seconds",
        "Cumulative seconds spent waiting for AI provider responses.",
        registry=registry,
    ).inc(_finite_number(counters.get("ai_provider_latency_seconds_total", 0)))
    Counter(
        "stella_ai_provider_latency_observations",
        "Number of AI provider request latency observations.",
        registry=registry,
    ).inc(_finite_number(counters.get("ai_provider_latency_observations", 0)))
    Gauge(
        "stella_message_send_retry_wait_seconds",
        "Cumulative seconds spent waiting before outgoing message retries.",
        registry=registry,
    ).set(_finite_number(counters.get("message_send_retry_wait_seconds", 0)))

    Counter(
        "stella_external_requests",
        "External source requests made by this process.",
        registry=registry,
    ).inc(external_requests)
    Counter(
        "stella_external_failures",
        "External source failures recorded by this process.",
        registry=registry,
    ).inc(external_failures)
    Counter(
        "stella_tts_messages_enqueued",
        "Priority and regular TTS messages enqueued by this process.",
        registry=registry,
    ).inc(
        _finite_number(counters.get("tts_priority_enqueued", 0))
        + _finite_number(counters.get("tts_regular_enqueued", 0))
    )

    Gauge(
        "stella_process_uptime_seconds",
        "Seconds since this stella process started.",
        registry=registry,
    ).set(_finite_number(uptime_seconds))
    if memory_bytes is not None:
        Gauge(
            "stella_process_memory_bytes",
            "Resident memory used by the stella process.",
            registry=registry,
        ).set(_finite_number(memory_bytes))
    if cpu_percent is not None:
        Gauge(
            "stella_process_cpu_percent",
            "CPU percentage reported for the stella process.",
            registry=registry,
        ).set(_finite_number(cpu_percent))

    return generate_latest(registry), CONTENT_TYPE_LATEST
