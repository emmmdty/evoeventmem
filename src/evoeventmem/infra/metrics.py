from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import TypeVar

_DEFAULT_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0)

T = TypeVar("T", bound="_BaseMetric")


class MetricError(ValueError):
    pass


class _BaseMetric:
    def __init__(self, name: str, help_text: str, labels: Sequence[str]) -> None:
        if not name.replace("_", "").isalnum():
            raise MetricError(f"invalid metric name: {name!r}")
        self.name = name
        self.help = help_text
        self.labels = tuple(labels)
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, str], float] = {}

    def _key(self, label_values: Mapping[str, str]) -> tuple[str, str]:
        missing = [label for label in self.labels if label not in label_values]
        if missing:
            raise MetricError(f"missing label values for {missing}")
        extra = [key for key in label_values if key not in self.labels]
        if extra:
            raise MetricError(f"unknown label values for {extra}")
        return (self.name, _render_labels(label_values))


class Counter(_BaseMetric):
    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        key = self._key(labels or {})
        with self._lock:
            self._samples[key] = self._samples.get(key, 0.0) + amount

    def value(self, labels: Mapping[str, str] | None = None) -> float:
        key = self._key(labels or {})
        with self._lock:
            return self._samples.get(key, 0.0)


class Histogram(_BaseMetric):
    def __init__(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = _DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(float(bucket) for bucket in buckets))

    def observe(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        label_values = labels or {}
        with self._lock:
            count_key = self._key(label_values)
            self._samples[count_key] = self._samples.get(count_key, 0.0) + 1
            sum_key = (self.name + "_sum", _render_labels(label_values))
            self._samples[sum_key] = self._samples.get(sum_key, 0.0) + value
            for bucket in self.buckets:
                if value <= bucket:
                    bucket_key = (
                        self.name + "_bucket",
                        _render_labels({**label_values, "le": f"{bucket:g}"}),
                    )
                    self._samples[bucket_key] = self._samples.get(bucket_key, 0.0) + 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for (name_key, label_suffix), value in sorted(self._samples.items()):
            if name_key == self.name:
                continue
            lines.append(f"{name_key}{label_suffix} {value:.6g}")
        return lines


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, _BaseMetric] = {}

    def counter(self, name: str, help_text: str, labels: Sequence[str] = ()) -> Counter:
        return self._register(Counter(name, help_text, labels))

    def histogram(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = _DEFAULT_BUCKETS,
    ) -> Histogram:
        return self._register(Histogram(name, help_text, labels, buckets))

    def _register(self, metric: T) -> T:
        if metric.name in self._metrics:
            raise MetricError(f"duplicate metric: {metric.name}")
        self._metrics[metric.name] = metric
        return metric

    def render_text(self) -> str:
        lines: list[str] = []
        for name in sorted(self._metrics):
            metric = self._metrics[name]
            if isinstance(metric, Counter):
                lines.append(f"# HELP {metric.name} {metric.help}")
                lines.append(f"# TYPE {metric.name} counter")
                for (_, label_suffix), value in sorted(metric._samples.items()):
                    lines.append(f"{metric.name}{label_suffix} {value:.6g}")
            elif isinstance(metric, Histogram):
                lines.extend(metric.render())
        return "\n".join(lines) + ("\n" if lines else "")


def _render_labels(label_values: Mapping[str, str]) -> str:
    rendered = ",".join(
        f'{key}="{label_values[key]}"' for key in sorted(label_values)
    )
    return "{" + rendered + "}" if rendered else ""
