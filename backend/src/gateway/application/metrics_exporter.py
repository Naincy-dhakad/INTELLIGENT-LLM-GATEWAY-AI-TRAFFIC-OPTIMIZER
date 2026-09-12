"""Technology-neutral metric exporter contract and deterministic text renderer."""

from typing import Protocol

from gateway.application.metrics import CounterSnapshot, HistogramSnapshot, MetricsSnapshot


class MetricsExporter(Protocol):
    """Read-only exporter boundary; implementations consume snapshots only."""

    def export(self, snapshot: MetricsSnapshot) -> str:
        ...


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape_label(value)}"' for key, value in labels) + "}"


def _number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return format(value, ".15g")


class TextMetricsExporter:
    """Deterministic Prometheus-compatible text foundation without a dependency."""

    def export(self, snapshot: MetricsSnapshot) -> str:
        lines: list[str] = []
        counters = sorted(snapshot.counters, key=lambda item: (item.definition.name, item.labels))
        histograms = sorted(snapshot.histograms, key=lambda item: (item.definition.name, item.labels))
        current_name = None
        for item in counters:
            if item.definition.name != current_name:
                self._append_header(lines, item.definition.name, item.definition.description, "counter")
                current_name = item.definition.name
            lines.append(f"{item.definition.name}{_labels_text(item.labels)} {_number(item.value)}")
        current_name = None
        for item in histograms:
            if item.definition.name != current_name:
                self._append_header(lines, item.definition.name, item.definition.description, "histogram")
                current_name = item.definition.name
            self._append_histogram_samples(lines, item)
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _append_header(lines: list[str], name: str, description: str, metric_type: str) -> None:
        lines.append(f"# HELP {name} {description}")
        lines.append(f"# TYPE {name} {metric_type}")

    @staticmethod
    def _append_histogram_samples(lines: list[str], item: HistogramSnapshot) -> None:
        definition = item.definition
        for bucket, count in item.bucket_counts:
            labels = item.labels + (("le", _number(bucket)),)
            lines.append(f"{definition.name}_bucket{_labels_text(labels)} {count}")
        infinity_labels = item.labels + (("le", "+Inf"),)
        lines.append(f"{definition.name}_bucket{_labels_text(infinity_labels)} {item.count}")
        lines.append(f"{definition.name}_sum{_labels_text(item.labels)} {_number(item.sum)}")
        lines.append(f"{definition.name}_count{_labels_text(item.labels)} {item.count}")
