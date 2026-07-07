from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_CONFIGURED = False
_ENABLED = False
_DOTENV_LOADED = False
_LAST_ERROR: str | None = None


def configure_langfuse() -> bool:
    """Configure Langfuse/OpenTelemetry once when credentials are available."""
    global _CONFIGURED, _ENABLED, _LAST_ERROR
    if _CONFIGURED:
        return _ENABLED

    _load_dotenv()
    _CONFIGURED = True
    if not _langfuse_should_enable():
        _ENABLED = False
        return False

    try:
        from openinference.instrumentation.claude_agent_sdk import ClaudeAgentSDKInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        _ENABLED = False
        return False

    _configure_otel_env()

    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "vp-agent")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    try:
        ClaudeAgentSDKInstrumentor().instrument()
    except Exception as exc:
        # Keep app startup resilient if instrumentation is already active or unavailable.
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        pass

    _ENABLED = True
    return True


def langfuse_enabled() -> bool:
    return configure_langfuse()


def langfuse_status() -> dict[str, Any]:
    _load_dotenv()
    enabled = configure_langfuse()
    return {
        "enabled": enabled,
        "configured": _CONFIGURED,
        "dotenv_loaded": _DOTENV_LOADED,
        "public_key_present": bool(os.getenv("LANGFUSE_PUBLIC_KEY")),
        "secret_key_present": bool(os.getenv("LANGFUSE_SECRET_KEY")),
        "base_url": os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_URL"),
        "otel_service_name": os.getenv("OTEL_SERVICE_NAME"),
        "otel_endpoint": os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        "otel_headers_present": bool(
            os.getenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        ),
        "last_error": _LAST_ERROR,
    }


@contextmanager
def observation(name: str, **attributes: Any) -> Iterator[Any]:
    if not configure_langfuse():
        yield None
        return

    from opentelemetry import trace

    tracer = trace.get_tracer("vp-agent")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            _set_attribute(span, key, value)
        yield span


def update_observation(span: Any, **attributes: Any) -> None:
    if span is None:
        return
    for key, value in attributes.items():
        _set_attribute(span, key, value)


def record_exception(span: Any, exc: BaseException) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        span.set_attribute("error.type", type(exc).__name__)
        span.set_attribute("error.message", str(exc))
    except Exception:
        pass


def _langfuse_should_enable() -> bool:
    flag = os.getenv("LANGFUSE_ENABLED")
    if flag is not None and flag.lower() in {"0", "false", "no", "off"}:
        return False
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return True
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _configure_otel_env() -> None:
    base_url = (
        os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_URL")
        or "https://cloud.langfuse.com"
    ).rstrip("/")

    os.environ.setdefault("OTEL_SERVICE_NAME", "vp-agent")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", f"{base_url}/api/public/otel")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"{base_url}/api/public/otel/v1/traces")

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if public_key and secret_key:
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        headers = f"Authorization=Basic {auth},x-langfuse-ingestion-version=4"
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            headers,
        )
        os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", headers)


def _load_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(env_path, override=False)


def _set_attribute(span: Any, key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, (str, bool, int, float)):
        span.set_attribute(key, value)
        return
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        span.set_attribute(key, value)
        return
    span.set_attribute(key, json.dumps(value, sort_keys=True, default=str))
