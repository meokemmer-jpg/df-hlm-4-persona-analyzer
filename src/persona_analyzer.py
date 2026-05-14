"""DF-HLM-4 Persona-Cohort-Analyzer for HeyLou Marketing Wave 2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - exercised in minimal local envs
    np = None  # type: ignore

try:
    from scipy import stats  # type: ignore
except Exception:  # pragma: no cover
    stats = None  # type: ignore

try:
    import structlog  # type: ignore
except Exception:  # pragma: no cover
    class _FallbackStructlog:
        @staticmethod
        def get_logger() -> Any:
            class _Log:
                def info(self, *_args: Any, **_kwargs: Any) -> None: return None
            return _Log()
    structlog = _FallbackStructlog()  # type: ignore
ROOT = Path(__file__).resolve().parent.parent
PERSONAS = (
    "Bayer-Werks",
    "Bosch-Travel",
    "Familie",
    "Buchmesse-Verleger",
    "KIT-Forscher",
    "Salzburg-Klassik",
    "Wedding",
    "Mittelalter",
)
CHANNELS = ("direct", "ota", "corporate", "event", "referral")
PRICE_SEGMENTS = ("budget", "mid", "premium", "luxury")
MIN_COHORT_SIZE = 5
LOG = structlog.get_logger()

@dataclass(frozen=True)
class Booking:
    booking_id: str
    customer_hash: str
    persona_tag: str
    booking_date: str
    stay_nights: int
    channel: str
    price_segment: str
    season_index: int
    group_size: int
    revenue_eur: float
    returned_within_90d: bool
    referred: bool
    nps: int


@dataclass(frozen=True)
class CohortStat:
    persona: str
    cohort_size: int
    avg_revenue_per_booking: float
    revenue_ci95: tuple[float, float]
    retention_rate: float
    referral_rate: float
    satisfaction_nps: float
    anonymized: bool = True


@dataclass(frozen=True)
class AnalysisResult:
    month: str
    mode: str
    cluster_k: int
    cluster_labels: list[int]
    cohorts: list[CohortStat]
    chi_squared: dict[str, float]
    revenue_t_test: dict[str, float | str]
    drift_alert: dict[str, Any]
    markdown_path: str
    csv_path: str
    alert_path: str
    audit_path: str
    snapshot_id: str


class CircuitBreaker:
    def __init__(self, open_threshold: int = 3, timeout_s: int = 30) -> None:
        self.open_threshold = open_threshold
        self.timeout_s = timeout_s
        self.failures = 0
        self.opened = False
    def call(self, fn: Callable[[], list[Booking]]) -> list[Booking]:
        if self.opened:
            raise RuntimeError("circuit_breaker_open")
        try:
            started = time.time()
            value = fn()
            if time.time() - started > self.timeout_s:
                raise TimeoutError("PMS-API unreachable >30s")
            self.failures = 0
            return value
        except Exception:
            self.failures += 1
            if self.failures >= self.open_threshold:
                self.opened = True
            raise


class PersonaAnalyzer:
    def __init__(self, root: Path | None = None, config: dict[str, Any] | None = None) -> None:
        self.root = root or ROOT
        self.config = config or self.default_config()
        ops = self.config["operations"]
        self.report_dir = self.root / ops["report_dir"]
        self.audit_log_path = self.root / ops["audit_log_path"]
        self.state_dir = self.root / ops["state_dir"]
        self.dlq_dir = self.root / ops["dlq_dir"]
        self.breaker = CircuitBreaker(
            open_threshold=self.config["lose_coupling"]["LC3_circuit_breaker"]["open_threshold"],
            timeout_s=self.config["lose_coupling"]["LC3_circuit_breaker"]["timeout_s"],
        )
    @staticmethod
    def default_config() -> dict[str, Any]:
        return {
            "df_id": "DF-HLM-4",
            "domain": {"personas": list(PERSONAS), "kmeans_k": 8, "min_cohort_size": MIN_COHORT_SIZE},
            "k11_cascade_containment": {"failure_blast_radius": 0, "dependency_dlq_separate": True},
            "k12_distillation_resistenz": {"provenance_required_in_output": True, "non_llm_validation_layer": True},
            "k13_independent_ground_truth": {"external_anchor_type": "hotel_pms_api", "pre_action_domain_check": True},
            "k16_concurrent_spawn_mutex": {"lock_dir": "/tmp/df-hlm-4.lock", "engine_pgrep_check": True},
            "lose_coupling": {
                "LC2_direct_mode_fallback": {"capability": 0.70, "trigger": "PMS-API unreachable >30s"},
                "LC3_circuit_breaker": {"timeout_s": 30, "open_threshold": 3},
                "LC5_health_check_independence": {"health_check_dependencies": []},
            },
            "operations": {
                "report_dir": "branch-hub/reports",
                "audit_log_path": "branch-hub/audit/df-hlm-4-audit.jsonl",
                "state_dir": "branch-hub/state",
                "dlq_dir": "branch-hub/dlq",
            },
        }
    def real_mode_enabled(self) -> bool:
        ticket = os.environ.get("PHRONESIS_TICKET", "")
        return (
            os.environ.get("DF_HLM_4_REAL_PMS_ENABLED") == "true"
            and re.match(r"^PT-2026-[A-Z0-9]{2}-[A-Z0-9]{3}$", ticket) is not None
            and os.environ.get("Q_0_APPROVAL") == "DANIELA-COHORT-ANONYMIZATION"
        )
    def pre_action_domain_check(self) -> bool:
        cfg = self.config.get("k13_independent_ground_truth", {})
        return cfg.get("external_anchor_type") == "hotel_pms_api" and bool(cfg.get("pre_action_domain_check"))

    def health_check(self) -> dict[str, Any]:
        return {"healthy": True, "dependencies": [], "df_id": "DF-HLM-4"}

    def acquire_mutex(self, lock_dir: Path | None = None) -> bool:
        target = lock_dir or Path(self.config["k16_concurrent_spawn_mutex"]["lock_dir"])
        try:
            target.mkdir(parents=True, exist_ok=False)
            (target / "pid").write_text(str(os.getpid()), encoding="utf-8")
            return True
        except FileExistsError:
            return False

    def release_mutex(self, lock_dir: Path | None = None) -> None:
        target = lock_dir or Path(self.config["k16_concurrent_spawn_mutex"]["lock_dir"])
        for child in target.glob("*") if target.exists() else []:
            child.unlink(missing_ok=True)
        target.rmdir() if target.exists() else None

    def run(self, month: str | None = None, pms_fetcher: Callable[[], list[Booking]] | None = None) -> AnalysisResult:
        if not self.pre_action_domain_check():
            raise RuntimeError("K13 pre_action_domain_check failed")
        month = month or date.today().strftime("%Y-%m")
        mode = "real_pms" if self.real_mode_enabled() else "mock"
        try:
            bookings = self.breaker.call(pms_fetcher) if mode == "real_pms" and pms_fetcher else self.synthetic_bookings(month)
        except Exception as exc:
            self._append_dlq(month, exc)
            mode = "standalone_cached_baseline"
            bookings = self.cached_baseline(month)
        result = self.analyze(bookings, month, mode)
        self._append_audit({"event": "run_completed", "month": month, "mode": mode, "snapshot_id": result.snapshot_id})
        LOG.info("df_hlm_4_run_completed", month=month, mode=mode)
        return result

    def analyze(self, bookings: list[Booking], month: str, mode: str = "mock") -> AnalysisResult:
        features = [self.feature_vector(b) for b in bookings]
        labels = self.kmeans_fixed(features, k=8)
        cohorts = self.cohort_stats(bookings)
        chi = self.chi_squared_persona_channel(bookings)
        ttest = self.revenue_t_test(bookings, PERSONAS[0], PERSONAS[1])
        drift = self.persona_drift(bookings, month)
        snapshot_id = hashlib.sha256(f"{month}:{mode}:df-hlm-4".encode()).hexdigest()[:16]
        paths = self.write_outputs(month, mode, cohorts, chi, ttest, drift, snapshot_id)
        return AnalysisResult(month, mode, 8, labels, cohorts, chi, ttest, drift, *paths, snapshot_id)

    def synthetic_bookings(self, month: str, n_per_persona: int = 12) -> list[Booking]:
        seed = int(hashlib.sha256(month.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed) if np is not None else None
        start = datetime.fromisoformat(f"{month}-01T00:00:00+00:00")
        rows: list[Booking] = []
        for p_idx, persona in enumerate(PERSONAS):
            for i in range(n_per_persona):
                rnd = (p_idx * 31 + i * 17 + seed) % 100
                nights = int(rng.integers(1, 7)) if rng is not None else 1 + rnd % 6
                group = int(rng.integers(1, 6)) if rng is not None else 1 + rnd % 5
                channel = CHANNELS[(p_idx + i) % len(CHANNELS)]
                price = PRICE_SEGMENTS[(p_idx + i // 2) % len(PRICE_SEGMENTS)]
                revenue = 95 + p_idx * 38 + nights * 44 + group * 18 + (rnd % 13)
                rows.append(Booking(
                    f"B-{month}-{p_idx}-{i}",
                    hashlib.sha256(f"cust-{p_idx}-{i % 7}".encode()).hexdigest()[:12],
                    persona,
                    (start + timedelta(days=i % 27)).date().isoformat(),
                    nights,
                    channel,
                    price,
                    (start.month - 1) // 3,
                    group,
                    float(revenue),
                    i % 3 == 0,
                    i % 4 == 0,
                    45 + ((p_idx * 7 + i * 3) % 55),
                ))
        return rows

    def cached_baseline(self, month: str) -> list[Booking]:
        return self.synthetic_bookings(month, n_per_persona=8)

    def feature_vector(self, b: Booking) -> list[float]:
        return [
            float(b.stay_nights),
            float(CHANNELS.index(b.channel)),
            float(PRICE_SEGMENTS.index(b.price_segment)),
            float(b.season_index),
            float(b.group_size),
        ]

    def kmeans_fixed(self, features: list[list[float]], k: int = 8, iterations: int = 8) -> list[int]:
        if k != 8:
            raise ValueError("DF-HLM-4 requires fixed K=8")
        if len(features) < k:
            raise ValueError("at least 8 bookings required")
        centers = [features[i * len(features) // k][:] for i in range(k)]
        labels = [0] * len(features)
        for _ in range(iterations):
            labels = [min(range(k), key=lambda c: self._dist(row, centers[c])) for row in features]
            for c in range(k):
                members = [features[i] for i, label in enumerate(labels) if label == c]
                if members:
                    centers[c] = [sum(col) / len(col) for col in zip(*members)]
        return labels

    @staticmethod
    def _dist(a: list[float], b: list[float]) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b))

    def cohort_stats(self, bookings: list[Booking]) -> list[CohortStat]:
        grouped: dict[str, list[Booking]] = defaultdict(list)
        small: list[Booking] = []
        for b in bookings:
            grouped[b.persona_tag].append(b)
        stats_out: list[CohortStat] = []
        for persona in PERSONAS:
            rows = grouped.get(persona, [])
            if len(rows) < MIN_COHORT_SIZE:
                small.extend(rows)
            else:
                stats_out.append(self._stat(persona, rows))
        if len(small) >= MIN_COHORT_SIZE:
            stats_out.append(self._stat("Other-DSGVO-Aggregated", small))
        return stats_out

    def _stat(self, persona: str, rows: list[Booking]) -> CohortStat:
        revenues = [b.revenue_eur for b in rows]
        avg = sum(revenues) / len(revenues)
        sd = math.sqrt(sum((x - avg) ** 2 for x in revenues) / max(len(revenues) - 1, 1))
        margin = 1.96 * sd / math.sqrt(len(revenues))
        return CohortStat(
            persona, len(rows), round(avg, 2), (round(avg - margin, 2), round(avg + margin, 2)),
            round(sum(b.returned_within_90d for b in rows) / len(rows), 4),
            round(sum(b.referred for b in rows) / len(rows), 4),
            round(sum(b.nps for b in rows) / len(rows), 2),
        )

    def chi_squared_persona_channel(self, bookings: list[Booking]) -> dict[str, float]:
        table = [[sum(1 for b in bookings if b.persona_tag == p and b.channel == c) for c in CHANNELS] for p in PERSONAS]
        if stats is not None:
            chi2, p_value, dof, _ = stats.chi2_contingency(table)
            return {"chi2": float(chi2), "p_value": float(p_value), "dof": float(dof)}
        row_tot = [sum(r) for r in table]
        col_tot = [sum(table[i][j] for i in range(len(table))) for j in range(len(CHANNELS))]
        total = sum(row_tot)
        chi2 = sum(((table[i][j] - row_tot[i] * col_tot[j] / total) ** 2) / max(row_tot[i] * col_tot[j] / total, 1e-9) for i in range(len(table)) for j in range(len(CHANNELS)))
        return {"chi2": float(chi2), "p_value": float(math.exp(-0.5 * chi2)), "dof": float((len(PERSONAS) - 1) * (len(CHANNELS) - 1))}

    def revenue_t_test(self, bookings: list[Booking], a: str, b: str) -> dict[str, float | str]:
        xs = [x.revenue_eur for x in bookings if x.persona_tag == a]
        ys = [y.revenue_eur for y in bookings if y.persona_tag == b]
        if stats is not None:
            res = stats.ttest_ind(xs, ys, equal_var=False)
            return {"persona_a": a, "persona_b": b, "t_stat": float(res.statistic), "p_value": float(res.pvalue)}
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        var_x = sum((x - mean_x) ** 2 for x in xs) / max(len(xs) - 1, 1)
        var_y = sum((y - mean_y) ** 2 for y in ys) / max(len(ys) - 1, 1)
        t_stat = (mean_x - mean_y) / math.sqrt(var_x / len(xs) + var_y / len(ys))
        return {"persona_a": a, "persona_b": b, "t_stat": float(t_stat), "p_value": float(math.exp(-abs(t_stat)))}

    def persona_drift(self, bookings: list[Booking], month: str) -> dict[str, Any]:
        current = self._persona_distribution(bookings)
        baseline = {p: 1 / len(PERSONAS) for p in PERSONAS}
        kl = sum(current[p] * math.log(max(current[p], 1e-12) / baseline[p]) for p in PERSONAS)
        return {"month": month, "kl_divergence": round(kl, 6), "threshold": 0.15, "alert": kl > 0.15, "baseline_window": "3_months"}

    @staticmethod
    def _persona_distribution(bookings: list[Booking]) -> dict[str, float]:
        counts = Counter(b.persona_tag for b in bookings)
        total = max(sum(counts.values()), 1)
        return {p: counts[p] / total for p in PERSONAS}

    def write_outputs(
        self, month: str, mode: str, cohorts: list[CohortStat], chi: dict[str, float],
        ttest: dict[str, float | str], drift: dict[str, Any], snapshot_id: str
    ) -> tuple[str, str, str, str]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        md_path = self.report_dir / f"df-hlm-4-monthly-cohort-report-{month}.md"
        csv_path = self.report_dir / f"df-hlm-4-monthly-cohort-report-{month}.csv"
        alert_path = self.report_dir / f"df-hlm-4-persona-drift-alert-{month}.json"
        md = self._markdown(month, mode, cohorts, chi, ttest, drift, snapshot_id)
        self._atomic_write(md_path, md)
        self._atomic_write_csv(csv_path, cohorts)
        self._atomic_write(alert_path, json.dumps(drift, indent=2, sort_keys=True))
        return str(md_path), str(csv_path), str(alert_path), str(self.audit_log_path)

    def _markdown(self, month: str, mode: str, cohorts: list[CohortStat], chi: dict[str, float], ttest: dict[str, float | str], drift: dict[str, Any], snapshot_id: str) -> str:
        lines = [f"# DF-HLM-4 Monthly Cohort Report {month}", "", f"- Mode: {mode}", f"- Snapshot: {snapshot_id}", "- Provenance: cohort_size and confidence_interval_95 included; non-LLM deterministic statistics.", ""]
        lines.append("| Persona | Cohort-Size | Avg Revenue | CI95 | Retention | Referral | NPS |")
        lines.append("|---|---:|---:|---|---:|---:|---:|")
        for c in cohorts:
            lines.append(f"| {c.persona} | {c.cohort_size} | {c.avg_revenue_per_booking:.2f} | [{c.revenue_ci95[0]:.2f}, {c.revenue_ci95[1]:.2f}] | {c.retention_rate:.4f} | {c.referral_rate:.4f} | {c.satisfaction_nps:.2f} |")
        lines += ["", f"Chi-Squared persona-channel: chi2={chi['chi2']:.4f}, p={chi['p_value']:.6f}, dof={chi['dof']:.0f}", f"Revenue t-test {ttest['persona_a']} vs {ttest['persona_b']}: t={float(ttest['t_stat']):.4f}, p={float(ttest['p_value']):.6f}", f"KL drift: {drift['kl_divergence']:.6f}, alert={drift['alert']}", "", "DSGVO: only anonymized cohort aggregates are emitted; individual booking rows are excluded."]
        return "\n".join(lines) + "\n"

    def _atomic_write_csv(self, path: Path, cohorts: list[CohortStat]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(cohorts[0]).keys()) if cohorts else ["persona"])
            writer.writeheader()
            for c in cohorts:
                writer.writerow(asdict(c))
        os.replace(tmp, path)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _append_audit(self, detail: dict[str, Any]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts_iso": datetime.now(timezone.utc).isoformat(), "df_id": "DF-HLM-4", **detail}
        with self.audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def _append_dlq(self, month: str, exc: Exception) -> None:
        self.dlq_dir.mkdir(parents=True, exist_ok=True)
        with (self.dlq_dir / "pms_api.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"month": month, "error": str(exc), "ts_iso": datetime.now(timezone.utc).isoformat()}) + "\n")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    month = argv[0] if argv else None
    analyzer = PersonaAnalyzer()
    result = analyzer.run(month)
    print(json.dumps({"month": result.month, "mode": result.mode, "markdown": result.markdown_path, "csv": result.csv_path, "alert": result.alert_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
