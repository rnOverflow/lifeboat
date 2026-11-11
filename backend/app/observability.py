"""Prometheus metrics and OpenTelemetry tracing."""

from __future__ import annotations

import logging

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

log = logging.getLogger(__name__)

REGISTRY = CollectorRegistry(auto_describe=True)

REGION_UP = Gauge("lifeboat_region_up", "1 if region healthy per monitor", ["region", "cloud"], registry=REGISTRY)
REGION_STATUS = Gauge("lifeboat_region_status", "0=up 1=degraded 2=down", ["region"], registry=REGISTRY)
PROBE_LATENCY = Histogram(
    "lifeboat_probe_latency_ms",
    "Region probe latency",
    ["region"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 5000),
    registry=REGISTRY,
)
REPLICATION_LAG = Gauge("lifeboat_replication_lag_seconds", "Replica lag behind primary", ["region"], registry=REGISTRY)
RPO_EXPOSURE = Gauge("lifeboat_rpo_exposure_seconds", "Data-loss window if primary failed now", registry=REGISTRY)
READINESS = Gauge("lifeboat_readiness_score", "DR readiness score 0-100", registry=REGISTRY)
ACTIVE_REGION = Gauge("lifeboat_active_region", "1 for the region receiving traffic", ["region"], registry=REGISTRY)
BACKUP_AGE = Gauge("lifeboat_last_verified_backup_age_seconds", "Age of newest verified backup", registry=REGISTRY)
RUNS = Counter("lifeboat_recovery_runs_total", "Recovery runs finished", ["kind", "status"], registry=REGISTRY)
LAST_RTO = Gauge("lifeboat_last_rto_seconds", "Measured RTO of the last run", ["kind"], registry=REGISTRY)
LAST_RPO = Gauge("lifeboat_last_rpo_seconds", "Measured RPO of the last run", ["kind"], registry=REGISTRY)
INCIDENTS_OPEN = Gauge("lifeboat_incidents_open", "Open incidents", registry=REGISTRY)
WORKLOAD_WRITES = Gauge("lifeboat_workload_writes_total", "Synthetic workload writes", ["result"], registry=REGISTRY)
TICK_DURATION = Histogram("lifeboat_engine_tick_seconds", "Engine tick duration", registry=REGISTRY)
HTTP_REQUESTS = Counter(
    "lifeboat_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)


def setup_tracing(app, endpoint: str | None, service_name: str = "lifeboat-api") -> None:  # noqa: ANN001
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):  # already configured (multiple app instances in one process)
        provider = current
        endpoint = None
    else:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
        log.info("OTLP trace export enabled -> %s", endpoint)
    if provider is not current:
        trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,healthz")
    except Exception:  # pragma: no cover
        log.exception("FastAPI instrumentation failed")


def tracer():  # noqa: ANN201
    from opentelemetry import trace

    return trace.get_tracer("lifeboat.engine")
