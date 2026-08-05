from __future__ import annotations

from evoeventmem.infra.metrics import Histogram, MetricError, MetricsRegistry


def test_counter_records_and_renders() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "evoeventmem_test_total",
        "test counter",
        labels=("method",),
    )
    counter.inc(labels={"method": "GET"})
    counter.inc(2, labels={"method": "GET"})
    counter.inc(labels={"method": "POST"})

    text = registry.render_text()

    assert "# TYPE evoeventmem_test_total counter" in text
    assert 'evoeventmem_test_total{method="GET"} 3' in text
    assert 'evoeventmem_test_total{method="POST"} 1' in text


def test_counter_default_value_is_zero() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("evoeventmem_zero_total", "zero counter", labels=("k",))
    assert counter.value(labels={"k": "v"}) == 0.0
    text = registry.render_text()
    assert "# TYPE evoeventmem_zero_total counter" in text
    assert 'evoeventmem_zero_total{k="v"}' not in text


def test_histogram_buckets_are_cumulative_and_labelled() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram(
        "evoeventmem_test_duration_seconds",
        "test histogram",
        labels=("method",),
        buckets=(0.1, 0.5, 1.0),
    )
    histogram.observe(0.05, labels={"method": "GET"})
    histogram.observe(0.3, labels={"method": "GET"})
    histogram.observe(2.0, labels={"method": "GET"})

    text = registry.render_text()

    assert "# TYPE evoeventmem_test_duration_seconds histogram" in text
    assert 'evoeventmem_test_duration_seconds_bucket{le="0.1",method="GET"} 1' in text
    assert 'evoeventmem_test_duration_seconds_bucket{le="0.5",method="GET"} 2' in text
    assert 'evoeventmem_test_duration_seconds_bucket{le="1",method="GET"} 2' in text
    assert 'evoeventmem_test_duration_seconds_sum{method="GET"} 2.35' in text


def test_duplicate_metric_name_is_rejected() -> None:
    registry = MetricsRegistry()
    registry.counter("evoeventmem_dup_total", "first")
    try:
        registry.counter("evoeventmem_dup_total", "second")
    except MetricError as exc:
        assert "duplicate metric" in str(exc)
    else:
        raise AssertionError("duplicate metric name was not rejected")


def test_missing_label_value_is_rejected() -> None:
    counter = MetricsRegistry().counter("evoeventmem_label_total", "labels", labels=("k",))
    try:
        counter.inc()
    except MetricError as exc:
        assert "missing label values" in str(exc)
    else:
        raise AssertionError("missing label value was not rejected")


def test_render_is_deterministically_ordered() -> None:
    registry = MetricsRegistry()
    registry.counter("evoeventmem_z_total", "z")
    registry.counter("evoeventmem_a_total", "a")
    first = registry.render_text()
    second = registry.render_text()
    assert first == second
    assert first.index("evoeventmem_a_total") < first.index("evoeventmem_z_total")


def test_histogram_rejects_invalid_names() -> None:
    try:
        Histogram("bad name!", "help")
    except MetricError as exc:
        assert "invalid metric name" in str(exc)
    else:
        raise AssertionError("invalid metric name was not rejected")
