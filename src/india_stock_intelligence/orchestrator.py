from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .agents.bull_bear_analyst import BullBearAnalyst
from .agents.independent_judge import IndependentJudge
from .agents.local_backend import LocalEvidenceAgentBackend
from .agents.research_analyst import EquityResearchAnalyst
from .agents.sample_backend import SampleAgentBackend
from .config import Settings, get_settings
from .evidence import EvidenceBundle, EvidenceRepository
from .llm.openai_backend import OpenAIResponsesBackend
from .retry import agent_retry_delay, is_retryable_agent_error
from .storage import connect_read_only, connect_read_write, json_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchRunResult:
    research_run_id: str
    security_key: str
    status: str
    verdict: str | None
    confidence_score: int | None
    risk_score: int | None
    report_path: str | None
    changes: dict
    evidence_digest: str


class ResearchOrchestrator:
    """Runs three isolated agents against the same immutable sourced bundle."""

    def __init__(self, database_path=None, settings: Settings | None = None, backend=None,
                 *, max_attempts: int = 1, allow_paid_retries: bool = False):
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if max_attempts > 1 and not allow_paid_retries and not isinstance(backend, (SampleAgentBackend, LocalEvidenceAgentBackend)):
            raise ValueError("Additional live attempts can cost extra; set allow_paid_retries=True explicitly")
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)
        self.backend = backend or OpenAIResponsesBackend(approved_for_this_run=False)
        self.max_attempts = max_attempts
        self.last_universe_manifest: dict = {}
        self.agents = {
            "equity_research_analyst": EquityResearchAnalyst(self.backend),
            "bull_bear_analyst": BullBearAnalyst(self.backend),
            "independent_judge": IndependentJudge(self.backend),
        }

    def run_security(self, security_key: str, *, as_of_date: str | None = None,
                     batch_run_id: str | None = None) -> ResearchRunResult:
        bundle = EvidenceRepository(self.database_path, self.settings).build_bundle(security_key, as_of_date)
        if isinstance(self.backend, SampleAgentBackend):
            if not bundle.sample_data:
                raise ValueError("Offline sample research is allowed only for securities explicitly marked as synthetic.")
            if bundle.evidence_items:
                raise ValueError("Offline sample research does not evaluate financial evidence; use an explicitly approved live backend.")
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        coverage = self._coverage(bundle)
        with connect_read_write(self.database_path) as conn:
            previous_row = conn.execute(
                """SELECT research_run_id, verdict, risk_score, confidence_score FROM research_runs
                   WHERE security_key=? ORDER BY started_at DESC LIMIT 1""", (security_key,)
            ).fetchone()
            conn.execute(
                """INSERT INTO research_runs
                   (research_run_id,security_key,as_of_date,status,evidence_digest,evidence_count,
                    evidence_coverage_json,sample_data,started_at,batch_run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, security_key, bundle.as_of_date, "running", bundle.digest,
                 len(bundle.evidence_items), json_text(coverage), int(bundle.sample_data), started_at,
                 batch_run_id),
            )
            conn.commit()

        outputs: dict[str, dict] = {}
        execution_meta: dict[str, dict] = {}
        errors: dict[str, str] = {}
        first = self._execute_agent(self.agents["equity_research_analyst"], bundle, {}, run_id)
        if first:
            outputs[first.agent_id] = first.output
            execution_meta[first.agent_id] = {"request_id": first.request_id, "model": first.model}
        else:
            errors["equity_research_analyst"] = "All configured attempts failed."

        bull_context = {"prior_reports": {"equity_research_analyst": outputs.get("equity_research_analyst")}}
        second = self._execute_agent(self.agents["bull_bear_analyst"], bundle, bull_context, run_id)
        if second:
            outputs[second.agent_id] = second.output
            execution_meta[second.agent_id] = {"request_id": second.request_id, "model": second.model}
        else:
            errors["bull_bear_analyst"] = "All configured attempts failed."

        if first and second:
            judge_context = {"complete_prior_reports": {
                "equity_research_analyst": outputs["equity_research_analyst"],
                "bull_bear_analyst": outputs["bull_bear_analyst"],
            }}
            judge = self._execute_agent(self.agents["independent_judge"], bundle, judge_context, run_id)
            if judge:
                outputs[judge.agent_id] = judge.output
                execution_meta[judge.agent_id] = {"request_id": judge.request_id, "model": judge.model}
            else:
                errors["independent_judge"] = "All configured attempts failed."
        else:
            errors["independent_judge"] = "Skipped because both complete first-pass reports were not available."
            self._save_skipped_judge(run_id, errors["independent_judge"])

        judge_output = outputs.get("independent_judge")
        status = "completed" if len(outputs) == 3 else "incomplete"
        changes = self._changes(previous_row, judge_output)
        finished_at = datetime.now(timezone.utc).isoformat()
        with connect_read_write(self.database_path) as conn:
            conn.execute(
                """UPDATE research_runs SET status=?,finished_at=?,verdict=?,risk_score=?,confidence_score=?,
                   watchlist_action=?,changes_json=? WHERE research_run_id=?""",
                (status, finished_at, judge_output.get("verdict") if judge_output else None,
                 judge_output.get("risk_score") if judge_output else None,
                 judge_output.get("confidence_score") if judge_output else None,
                 judge_output.get("watchlist_action") if judge_output else None,
                 json_text(changes), run_id),
            )
            conn.commit()

        report_path = self._write_report(run_id, bundle, outputs, execution_meta, errors, changes, status)
        logger.info("Research run %s for %s finished with status=%s verdict=%s",
                    run_id, security_key, status, judge_output.get("verdict") if judge_output else "none")
        return ResearchRunResult(
            research_run_id=run_id, security_key=security_key, status=status,
            verdict=judge_output.get("verdict") if judge_output else None,
            confidence_score=judge_output.get("confidence_score") if judge_output else None,
            risk_score=judge_output.get("risk_score") if judge_output else None,
            report_path=report_path, changes=changes, evidence_digest=bundle.digest,
        )

    def run_universe(self, security_keys: list[str] | None = None, *, as_of_date: str | None = None,
                     sample_only: bool = False, batch_run_id: str | None = None,
                     resume: bool = False) -> list[ResearchRunResult]:
        if resume and not batch_run_id:
            raise ValueError("batch_run_id is required when resume=True")
        started_at = datetime.now(timezone.utc).isoformat()
        if resume:
            with connect_read_only(self.database_path) as conn:
                batch = conn.execute(
                    "SELECT * FROM universe_research_runs WHERE batch_run_id=?", (batch_run_id,)
                ).fetchone()
                if batch is None:
                    raise ValueError(f"Universe batch not found: {batch_run_id}")
                saved_keys = json.loads(batch["security_ids_json"])
                saved_as_of = batch["as_of_date"]
            if security_keys is not None and list(dict.fromkeys(security_keys)) != saved_keys:
                raise ValueError("A resumed batch must use its original security list")
            security_keys = saved_keys
            as_of_date = saved_as_of
        else:
            if batch_run_id is not None:
                raise ValueError("Pass resume=True when supplying an existing batch_run_id")
            if security_keys is None:
                with connect_read_only(self.database_path) as conn:
                    sample_filter = """WHERE EXISTS (SELECT 1 FROM source_records sr WHERE sr.security_key=s.security_key
                                        AND (lower(sr.source_name) LIKE '%sample%' OR lower(sr.source_name) LIKE '%synthetic%'))
                                       OR EXISTS (SELECT 1 FROM evidence_items e WHERE e.security_key=s.security_key AND e.is_synthetic=1)""" if sample_only else ""
                    security_keys = [row["security_key"] for row in conn.execute(
                        f"SELECT s.security_key FROM securities s {sample_filter} ORDER BY s.exchange,s.symbol"
                    ).fetchall()]
            security_keys = list(dict.fromkeys(security_keys))
            if as_of_date is None:
                with connect_read_only(self.database_path) as conn:
                    latest = conn.execute(
                        "SELECT MAX(price_date) FROM daily_prices WHERE close IS NOT NULL"
                    ).fetchone()[0]
                as_of_date = latest or date.today().isoformat()
            batch_run_id = str(uuid.uuid4())
            with connect_read_write(self.database_path) as conn:
                conn.execute(
                    """INSERT INTO universe_research_runs
                       (batch_run_id,as_of_date,status,total_securities,security_ids_json,started_at)
                       VALUES (?,?,?,?,?,?)""",
                    (batch_run_id, as_of_date, "running", len(security_keys),
                     json_text(security_keys), started_at),
                )
                conn.executemany(
                    """INSERT INTO universe_research_security_results
                       (batch_run_id,security_key,status) VALUES (?,?,'pending')""",
                    [(batch_run_id, key) for key in security_keys],
                )
                conn.commit()

        # Recover completed work whose process stopped between saving the security
        # run and updating its batch row. This makes a resumed batch idempotent.
        with connect_read_write(self.database_path) as conn:
            conn.execute(
                """UPDATE universe_research_security_results
                   SET status='completed', research_run_id=(
                       SELECT r.research_run_id FROM research_runs r
                       WHERE r.batch_run_id=universe_research_security_results.batch_run_id
                         AND r.security_key=universe_research_security_results.security_key
                         AND r.status='completed'
                       ORDER BY r.finished_at DESC LIMIT 1
                   ), error_summary=NULL, finished_at=COALESCE(finished_at, ?)
                   WHERE batch_run_id=? AND status!='completed'
                     AND EXISTS (
                       SELECT 1 FROM research_runs r
                       WHERE r.batch_run_id=universe_research_security_results.batch_run_id
                         AND r.security_key=universe_research_security_results.security_key
                         AND r.status='completed'
                     )""",
                (datetime.now(timezone.utc).isoformat(), batch_run_id),
            )
            conn.commit()

        results: list[ResearchRunResult] = []
        with connect_read_only(self.database_path) as conn:
            batch_security_rows = conn.execute(
                """SELECT security_key,status FROM universe_research_security_results
                   WHERE batch_run_id=? ORDER BY rowid""", (batch_run_id,)
            ).fetchall()
        for batch_security in batch_security_rows:
            security_key = batch_security["security_key"]
            if batch_security["status"] == "completed":
                continue
            attempt_started = datetime.now(timezone.utc).isoformat()
            with connect_read_write(self.database_path) as conn:
                conn.execute(
                    """UPDATE universe_research_security_results
                       SET status='running', error_summary=NULL, started_at=?, finished_at=NULL
                       WHERE batch_run_id=? AND security_key=?""",
                    (attempt_started, batch_run_id, security_key),
                )
                conn.commit()
            self._write_universe_manifest(batch_run_id)
            try:
                result = self.run_security(
                    security_key, as_of_date=as_of_date, batch_run_id=batch_run_id
                )
                results.append(result)
                if result.status == "completed":
                    result_status = "completed"
                    error_summary = None
                else:
                    result_status = "failed"
                    error_summary = "Research run completed with one or more agent failures."
            except Exception as exc:
                result_status = "failed"
                error_summary = f"{type(exc).__name__}: {exc}"[:1000]
                logger.exception("Universe research failed for %s in batch %s", security_key, batch_run_id)
            with connect_read_write(self.database_path) as conn:
                conn.execute(
                    """UPDATE universe_research_security_results
                       SET status=?, research_run_id=(
                           SELECT r.research_run_id FROM research_runs r
                           WHERE r.batch_run_id=? AND r.security_key=?
                           ORDER BY r.started_at DESC LIMIT 1
                       ), error_summary=?, finished_at=?
                       WHERE batch_run_id=? AND security_key=?""",
                    (result_status, batch_run_id, security_key, error_summary,
                     datetime.now(timezone.utc).isoformat(), batch_run_id, security_key),
                )
                conn.commit()
            self._write_universe_manifest(batch_run_id)

        self._write_universe_manifest(batch_run_id, complete=True)
        return results

    def _write_universe_manifest(self, batch_run_id: str, *, complete: bool = False) -> dict:
        with connect_read_write(self.database_path) as conn:
            batch = conn.execute(
                "SELECT * FROM universe_research_runs WHERE batch_run_id=?", (batch_run_id,)
            ).fetchone()
            rows = conn.execute(
                """SELECT security_key,status,research_run_id,error_summary
                   FROM universe_research_security_results
                   WHERE batch_run_id=? ORDER BY rowid""", (batch_run_id,)
            ).fetchall()
            attempted = [row["security_key"] for row in rows if row["status"] != "pending"]
            successful = [row["security_key"] for row in rows if row["status"] == "completed"]
            failed = [row["security_key"] for row in rows if row["status"] == "failed"]
            errors = [
                {"security_id": row["security_key"], "error_summary": row["error_summary"]}
                for row in rows if row["status"] == "failed"
            ]
            completed_at = datetime.now(timezone.utc).isoformat() if complete else None
            status = (
                "completed" if complete and not failed
                else "completed_with_failures" if complete
                else "running"
            )
            manifest = {
                "manifest_version": 1,
                "batch_run_id": batch_run_id,
                "as_of_date": batch["as_of_date"],
                "status": status,
                "total_securities": len(rows),
                "attempted_securities": len(attempted),
                "successful_securities": len(successful),
                "failed_securities": len(failed),
                "security_ids": [row["security_key"] for row in rows],
                "successful_security_ids": successful,
                "failed_security_ids": failed,
                "errors": errors,
                "started_at": batch["started_at"],
                "completion_timestamp": completed_at,
                "results": [
                    {"security_id": row["security_key"], "status": row["status"],
                     "research_run_id": row["research_run_id"], "error_summary": row["error_summary"]}
                    for row in rows
                ],
            }
            path = self.settings.report_dir / f"universe_research_{batch_run_id}.json"
            self.settings.report_dir.mkdir(parents=True, exist_ok=True)
            temporary_path = path.with_name(path.name + ".tmp")
            temporary_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary_path, path)
            conn.execute(
                """UPDATE universe_research_runs SET status=?,attempted_securities=?,
                   successful_securities=?,failed_securities=?,successful_security_ids_json=?,
                   failed_security_ids_json=?,errors_json=?,completed_at=?,manifest_path=?,manifest_json=?
                   WHERE batch_run_id=?""",
                (status, len(attempted), len(successful), len(failed), json_text(successful),
                 json_text(failed), json_text(errors), completed_at, str(path), json_text(manifest),
                 batch_run_id),
            )
            conn.commit()
        self.last_universe_manifest = manifest | {"manifest_path": str(path)}
        return self.last_universe_manifest

    def _execute_agent(self, agent, bundle: EvidenceBundle, context: dict, run_id: str):
        for attempt in range(1, self.max_attempts + 1):
            now = datetime.now(timezone.utc).isoformat()
            try:
                execution = agent.run(bundle, context)
                with connect_read_write(self.database_path) as conn:
                    conn.execute(
                        """INSERT INTO agent_outputs
                           (research_run_id,agent_id,attempt,status,schema_version,output_json,error_message,
                            request_id,model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, agent.agent_id, attempt, "completed", "1", json_text(execution.output), None,
                         execution.request_id, execution.model, now),
                    )
                    conn.commit()
                return execution
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:2000]
                logger.exception("Agent %s attempt %d/%d failed", agent.agent_id, attempt, self.max_attempts)
                with connect_read_write(self.database_path) as conn:
                    conn.execute(
                        """INSERT INTO agent_outputs
                           (research_run_id,agent_id,attempt,status,schema_version,output_json,error_message,
                            request_id,model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, agent.agent_id, attempt, "failed", "1", None, message, None,
                         getattr(self.backend, "model", None), now),
                    )
                    conn.commit()
                if attempt >= self.max_attempts or not is_retryable_agent_error(exc):
                    logger.warning(
                        "Agent %s will not be retried after non-transient failure type %s",
                        agent.agent_id, type(exc).__name__,
                    )
                    break
                delay = agent_retry_delay(exc, attempt)
                logger.warning(
                    "Retrying agent %s after classified transient failure (%d/%d) in %.2fs",
                    agent.agent_id, attempt, self.max_attempts, delay,
                )
                time.sleep(delay)
        return None

    def _save_skipped_judge(self, run_id: str, reason: str) -> None:
        with connect_read_write(self.database_path) as conn:
            conn.execute(
                """INSERT INTO agent_outputs
                   (research_run_id,agent_id,attempt,status,schema_version,error_message,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (run_id, "independent_judge", 0, "skipped", "1", reason, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    @staticmethod
    def _coverage(bundle: EvidenceBundle) -> dict:
        counts: dict[str, int] = {}
        for item in bundle.evidence_items:
            counts[item.source_type] = counts.get(item.source_type, 0) + 1
        return {"evidence_items": len(bundle.evidence_items), "by_source_type": counts,
                "missing_categories": list(bundle.missing_categories), "sample_data": bundle.sample_data,
                "market_screening_is_not_fundamental_evidence": True}

    @staticmethod
    def _changes(previous_row, judge_output: dict | None) -> dict:
        if previous_row is None:
            return {"previous_run_id": None, "changed": False, "note": "No earlier judge report exists."}
        previous = dict(previous_row)
        current_verdict = judge_output.get("verdict") if judge_output else None
        current_risk = judge_output.get("risk_score") if judge_output else None
        current_confidence = judge_output.get("confidence_score") if judge_output else None
        return {
            "previous_run_id": previous["research_run_id"],
            "previous_verdict": previous["verdict"],
            "verdict_changed": previous["verdict"] != current_verdict,
            "previous_risk_score": previous["risk_score"],
            "risk_score_delta": current_risk - previous["risk_score"] if current_risk is not None and previous["risk_score"] is not None else None,
            "previous_confidence_score": previous["confidence_score"],
            "confidence_score_delta": current_confidence - previous["confidence_score"] if current_confidence is not None and previous["confidence_score"] is not None else None,
        }

    def _write_report(self, run_id: str, bundle: EvidenceBundle, outputs: dict, execution_meta: dict,
                      errors: dict, changes: dict, status: str) -> str:
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        security_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", bundle.security["security_key"])
        label = "SAMPLE_" if bundle.sample_data else ""
        path = self.settings.report_dir / f"{label}research_{security_slug}_{bundle.as_of_date}_{run_id[:8]}.json"
        report = {
            "report_type": "three_agent_research",
            "sample_label": "SAMPLE DATA - SYNTHETIC; NOT EXCHANGE DATA" if bundle.sample_data else None,
            "research_run_id": run_id,
            "status": status,
            "security": bundle.security,
            "as_of_date": bundle.as_of_date,
            "evidence_digest": bundle.digest,
            "evidence_coverage": self._coverage(bundle),
            "source_references": [item.to_dict() for item in bundle.evidence_items],
            "agent_reports": outputs,
            "agent_execution": execution_meta,
            "agent_errors": errors,
            "changes_from_previous": changes,
            "trading_enabled": False,
        }
        encoded = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        path.write_text(encoded, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {"research_run_id": run_id, "evidence_digest": bundle.digest,
                    "source_references": [item.evidence_id for item in bundle.evidence_items],
                    "sample_data": bundle.sample_data, "status": status}
        with connect_read_write(self.database_path) as conn:
            conn.execute(
                "INSERT INTO generated_reports (report_type,file_path,sha256,data_date,generated_at,manifest_json) VALUES (?,?,?,?,?,?)",
                ("ai_research_json", str(path), digest, bundle.as_of_date, datetime.now(timezone.utc).isoformat(), json_text(manifest)),
            )
            conn.commit()
        return str(path)
