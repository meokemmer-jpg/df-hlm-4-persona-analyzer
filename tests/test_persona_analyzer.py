from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.persona_analyzer import Booking, PersonaAnalyzer, PERSONAS  # noqa: E402


@pytest.fixture
def analyzer(tmp_path: Path) -> PersonaAnalyzer:
    return PersonaAnalyzer(root=tmp_path)


@pytest.fixture
def engine(analyzer: PersonaAnalyzer) -> PersonaAnalyzer:
    return analyzer


def test_default_mock_mode_synthetic_data(analyzer: PersonaAnalyzer) -> None:
    result = analyzer.run("2026-05")
    assert result.mode == "mock"
    assert len(result.cohorts) == 8
    assert Path(result.markdown_path).exists()


def test_env_var_true_real_mode(monkeypatch: pytest.MonkeyPatch, analyzer: PersonaAnalyzer) -> None:
    monkeypatch.setenv("DF_HLM_4_REAL_PMS_ENABLED", "true")
    monkeypatch.setenv("PHRONESIS_TICKET", "PT-2026-XX-XXX")
    assert analyzer.real_mode_enabled() is False
    monkeypatch.setenv("Q_0_APPROVAL", "DANIELA-COHORT-ANONYMIZATION")
    assert analyzer.real_mode_enabled() is True


def test_concurrent_spawn_protection(tmp_path: Path, analyzer: PersonaAnalyzer) -> None:
    lock = tmp_path / "df-hlm-4.lock"
    assert analyzer.acquire_mutex(lock) is True
    assert analyzer.acquire_mutex(lock) is False
    analyzer.release_mutex(lock)


def test_cascade_containment(analyzer: PersonaAnalyzer) -> None:
    assert analyzer.config["k11_cascade_containment"]["failure_blast_radius"] == 0
    assert analyzer.config["k11_cascade_containment"]["dependency_dlq_separate"] is True


def test_external_anchor_pms(analyzer: PersonaAnalyzer) -> None:
    assert analyzer.config["k13_independent_ground_truth"]["external_anchor_type"] == "hotel_pms_api"


def test_circuit_breaker_open(analyzer: PersonaAnalyzer) -> None:
    for _ in range(3):
        with pytest.raises(RuntimeError):
            analyzer.breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert analyzer.breaker.opened is True


def test_direct_mode_cached_baseline(monkeypatch: pytest.MonkeyPatch, analyzer: PersonaAnalyzer) -> None:
    monkeypatch.setenv("DF_HLM_4_REAL_PMS_ENABLED", "true")
    monkeypatch.setenv("PHRONESIS_TICKET", "PT-2026-XX-XXX")
    monkeypatch.setenv("Q_0_APPROVAL", "DANIELA-COHORT-ANONYMIZATION")
    result = analyzer.run("2026-05", pms_fetcher=lambda: (_ for _ in ()).throw(TimeoutError("PMS-API unreachable >30s")))
    assert result.mode == "standalone_cached_baseline"
    assert len(result.cohorts) == 8


def test_idempotent_monthly_snapshot(analyzer: PersonaAnalyzer) -> None:
    a = analyzer.run("2026-05")
    b = analyzer.run("2026-05")
    assert a.snapshot_id == b.snapshot_id
    assert a.markdown_path == b.markdown_path


def test_health_check_no_deps(analyzer: PersonaAnalyzer) -> None:
    assert analyzer.health_check()["dependencies"] == []


def test_kmeans_8_personas_fixed(analyzer: PersonaAnalyzer) -> None:
    bookings = analyzer.synthetic_bookings("2026-05")
    labels = analyzer.kmeans_fixed([analyzer.feature_vector(b) for b in bookings], 8)
    assert len(set(labels)) == 8
    with pytest.raises(ValueError):
        analyzer.kmeans_fixed([analyzer.feature_vector(b) for b in bookings], 7)


def test_cohort_min_size_5(analyzer: PersonaAnalyzer) -> None:
    bookings = analyzer.synthetic_bookings("2026-05", n_per_persona=6)
    small = [b for b in bookings if b.persona_tag == PERSONAS[0]][:3]
    small += [b for b in bookings if b.persona_tag == PERSONAS[1]][:3]
    bookings = [b for b in bookings if b.persona_tag not in {PERSONAS[0], PERSONAS[1]}] + small
    cohorts = analyzer.cohort_stats(bookings)
    assert all(c.cohort_size >= 5 for c in cohorts)
    assert any(c.persona == "Other-DSGVO-Aggregated" for c in cohorts)


def test_chi_squared_persona_channel(analyzer: PersonaAnalyzer) -> None:
    chi = analyzer.chi_squared_persona_channel(analyzer.synthetic_bookings("2026-05"))
    assert chi["chi2"] >= 0
    assert 0 <= chi["p_value"] <= 1
    assert chi["dof"] > 0


def test_t_test_revenue_difference(analyzer: PersonaAnalyzer) -> None:
    res = analyzer.revenue_t_test(analyzer.synthetic_bookings("2026-05"), PERSONAS[0], PERSONAS[1])
    assert "t_stat" in res and "p_value" in res
    assert 0 <= float(res["p_value"]) <= 1


def test_kl_divergence_drift_detection(analyzer: PersonaAnalyzer) -> None:
    bookings = [b for b in analyzer.synthetic_bookings("2026-05") if b.persona_tag == PERSONAS[0]]
    drift = analyzer.persona_drift(bookings, "2026-05")
    assert drift["kl_divergence"] > drift["threshold"]
    assert drift["alert"] is True


def test_anonymization_no_individual_data_in_output(analyzer: PersonaAnalyzer) -> None:
    result = analyzer.run("2026-05")
    text = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "booking_id" not in text
    assert "customer_hash" not in text
    assert "B-2026-05" not in text


def test_provenance_in_output(analyzer: PersonaAnalyzer) -> None:
    result = analyzer.run("2026-05")
    text = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "Provenance" in text
    assert "Cohort-Size" in text
    assert "CI95" in text


def test_pre_action_domain_check(analyzer: PersonaAnalyzer) -> None:
    assert analyzer.pre_action_domain_check() is True
    analyzer.config["k13_independent_ground_truth"]["external_anchor_type"] = "wrong"
    with pytest.raises(RuntimeError):
        analyzer.run("2026-05")


def test_audit_log_appended_per_run(analyzer: PersonaAnalyzer) -> None:
    result = analyzer.run("2026-05")
    result2 = analyzer.run("2026-06")
    lines = Path(result.audit_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    assert json.loads(lines[-1])["snapshot_id"] == result2.snapshot_id


def test_pii_scrubbed_in_output_with_kemmer_name(engine: PersonaAnalyzer) -> None:
    """Output enthaelt keinen Kemmer-Familien-Namen."""

    original_markdown = engine._markdown

    def markdown_with_pii(*args: object, **kwargs: object) -> str:
        return f"{original_markdown(*args, **kwargs)}\nSensitive: Martin met Imke.\n"

    engine._markdown = markdown_with_pii  # type: ignore[method-assign]
    result = engine.run("2026-05")
    text = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "Martin" not in text
    assert "Imke" not in text


def test_k13_pre_action_verification_env_tag_block(engine: PersonaAnalyzer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-Mode mit falschem env_tag wird geblockt."""

    monkeypatch.setenv("DF_ENV_TAG", "prod")
    monkeypatch.setenv("DF_HLM_4_REAL_PMS_ENABLED", "true")
    with pytest.raises(RuntimeError) as exc_info:
        engine.run("2026-05")
    assert "K13" in str(exc_info.value)


def test_mock_provenance_explicit_in_output(engine: PersonaAnalyzer) -> None:
    """Mock-Outputs haben 'mode': 'mock' in Provenance."""

    result = engine.run("2026-05")
    text = Path(result.markdown_path).read_text(encoding="utf-8")
    assert '"mode": "mock"' in text or "MOCK-" in text


def test_k16_mutex_blocks_concurrent_spawn(tmp_path: Path) -> None:
    """Concurrent Engine-Spawn wird geblockt."""

    first = PersonaAnalyzer(root=tmp_path)
    second = PersonaAnalyzer(root=tmp_path)
    entered = threading.Event()
    errors: list[Exception] = []
    completed: list[str] = []
    lock_dir = Path("/tmp/df-hlm-4.lock")
    second.release_mutex(lock_dir)

    original_synthetic = first.synthetic_bookings

    def slow_synthetic(month: str, n_per_persona: int = 12) -> list[Booking]:
        entered.set()
        time.sleep(0.35)
        return original_synthetic(month, n_per_persona=n_per_persona)

    first.synthetic_bookings = slow_synthetic  # type: ignore[method-assign]

    def run_engine(analyzer: PersonaAnalyzer) -> None:
        try:
            completed.append(analyzer.run("2026-05").mode)
        except Exception as exc:  # pragma: no cover - assertion below inspects captured error
            errors.append(exc)

    thread_a = threading.Thread(target=run_engine, args=(first,))
    thread_b = threading.Thread(target=run_engine, args=(second,))

    thread_a.start()
    assert entered.wait(timeout=2)
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert completed == ["mock"]
    assert any("K16-VETO" in str(exc) for exc in errors)
    second.release_mutex(lock_dir)
