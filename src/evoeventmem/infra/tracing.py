"""OpenTelemetry tracing configuration for EvoEventMem.

Provides distributed tracing with optional Jaeger export for production monitoring.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import NoOpTracer

logger = logging.getLogger("evoeventmem")

# Default service name
SERVICE_NAME_EVOEVENTMEM = "evoeventmem"


def create_tracer_provider(
    service_name: str = SERVICE_NAME_EVOEVENTMEM,
    jaeger_endpoint: str | None = None,
    console_export: bool = False,
) -> TracerProvider:
    """Create and configure a TracerProvider with optional Jaeger export.

    Args:
        service_name: Service name for trace identification
        jaeger_endpoint: Jaeger collector endpoint URL (e.g., "http://localhost:14268/api/traces")
        console_export: If True, export traces to console for debugging

    Returns:
        Configured TracerProvider
    """
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    # Add Jaeger exporter if endpoint is provided
    if jaeger_endpoint:
        try:
            from opentelemetry.exporter.jaeger.thrift import JaegerExporter

            jaeger_exporter = JaegerExporter(
                collector_endpoint=jaeger_endpoint,
            )
            provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
            logger.info(
                "jaeger tracing configured",
                extra={"event": "tracing.jaeger", "endpoint": jaeger_endpoint},
            )
        except ImportError:
            logger.warning(
                "jaeger exporter not available; install opentelemetry-exporter-jaeger",
                extra={"event": "tracing.jaeger.missing"},
            )

    # Add console exporter for debugging
    if console_export:
        console_exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(console_exporter))
        logger.info(
            "console tracing enabled",
            extra={"event": "tracing.console"},
        )

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    return provider


def get_tracer(name: str = SERVICE_NAME_EVOEVENTMEM) -> trace.Tracer:
    """Get a tracer instance.

    Returns a NoOpTracer if no provider is configured.
    """
    try:
        return trace.get_tracer(name)
    except Exception:
        return NoOpTracer()


def setup_tracing(
    app: Any,
    service_name: str = SERVICE_NAME_EVOEVENTMEM,
    jaeger_endpoint: str | None = None,
    console_export: bool = False,
) -> None:
    """Set up OpenTelemetry tracing for a FastAPI application.

    Args:
        app: FastAPI application instance
        service_name: Service name for trace identification
        jaeger_endpoint: Jaeger collector endpoint URL
        console_export: If True, export traces to console
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # Create and configure tracer provider
        provider = create_tracer_provider(
            service_name=service_name,
            jaeger_endpoint=jaeger_endpoint,
            console_export=console_export,
        )

        # Instrument FastAPI
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

        logger.info(
            "opentelemetry tracing configured",
            extra={
                "event": "tracing.configured",
                "service_name": service_name,
                "jaeger_enabled": jaeger_endpoint is not None,
                "console_enabled": console_export,
            },
        )
    except ImportError:
        logger.warning(
            "opentelemetry instrumentation not available; tracing disabled",
            extra={"event": "tracing.disabled"},
        )
