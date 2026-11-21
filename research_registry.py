"""Append-only research hypothesis and experiment registry for ChartAnalyzer QRE."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1
MAX_SPEC_BYTES = 64 * 1024
MAX_METRICS_BYTES = 256 * 1024


class _ClosingSQLiteConnection(sqlite3.Connection):
    """Commit/rollback through the context manager, then always close the handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _safe_json(value):
    if isinstance(value, dict): return {str(k): _safe_json(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [_safe_json(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str,bytes)):
        try: return _safe_json(value.item())
        except Exception: return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "INF" if value > 0 else ("-INF" if value < 0 else None)
    return value


def canonical_hash(value: Any) -> str:
    raw = json.dumps(_safe_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ResearchRegistry:
    """SQLite registry. Hypothesis specifications are immutable; revisions get new IDs."""
    def __init__(self, path="research_registry.db"):
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _db(self):
        # Create/chmod the database before SQLite opens it, avoiding a permissive window.
        if os.name == "posix":
            if os.path.islink(self.path):
                raise OSError("SQLite database path must not be a symbolic link")
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        con = sqlite3.connect(self.path, timeout=15, factory=_ClosingSQLiteConnection)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        # The research registry contains experiment metadata and holdout lineage.
        if os.name == "posix":
            for candidate in (self.path, self.path + "-wal", self.path + "-shm"):
                try:
                    if os.path.exists(candidate):
                        os.chmod(candidate, 0o600)
                except OSError:
                    pass
        return con

    def _init(self):
        with self._db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS hypotheses(
              hypothesis_id TEXT PRIMARY KEY, name TEXT NOT NULL,
              specification_json TEXT NOT NULL, specification_hash TEXT NOT NULL,
              family_id TEXT NOT NULL, registered_at REAL NOT NULL,
              status TEXT NOT NULL DEFAULT 'REGISTERED'
            );
            CREATE TABLE IF NOT EXISTS experiments(
              experiment_id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL,
              dataset_id TEXT NOT NULL, config_hash TEXT NOT NULL,
              source TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
              run_id TEXT, sample_start REAL, sample_end REAL,
              metrics_json TEXT NOT NULL, p_value REAL, adjusted_p_value REAL,
              status TEXT NOT NULL, created_at REAL NOT NULL,
              FOREIGN KEY(hypothesis_id) REFERENCES hypotheses(hypothesis_id)
            );
            CREATE INDEX IF NOT EXISTS idx_exp_family ON experiments(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_exp_hyp ON experiments(hypothesis_id, created_at);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version','1');
            """)

    def register(self, hypothesis_id: str, name: str, specification: Dict[str, Any], family_id: Optional[str] = None):
        if not hypothesis_id or not name or not isinstance(specification, dict):
            raise ValueError("hypothesis_id, name and object specification are required")
        family = str(family_id or "default")
        if len(hypothesis_id) > 128 or len(name) > 256 or len(family) > 128:
            raise ValueError("hypothesis_id, name or family_id exceeds its length limit")
        spec = json.dumps(_safe_json(specification), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        spec_bytes = spec.encode("utf-8")
        if len(spec_bytes) > MAX_SPEC_BYTES:
            raise ValueError(f"hypothesis specification exceeds the {MAX_SPEC_BYTES // 1024} KiB limit")
        digest = hashlib.sha256(spec_bytes).hexdigest()
        with self._db() as c:
            old = c.execute(
                "SELECT name,family_id,specification_hash FROM hypotheses WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            if old:
                if old["specification_hash"] != digest or old["name"] != name or old["family_id"] != family:
                    raise ValueError(
                        "Hypothesis ID is immutable; use a new ID for changed name, family or specification"
                    )
                return {"hypothesis_id": hypothesis_id, "specification_hash": digest, "created": False}
            c.execute("INSERT INTO hypotheses VALUES(?,?,?,?,?,?,?)", (hypothesis_id, name, spec, digest, family, time.time(), "REGISTERED"))
        return {"hypothesis_id": hypothesis_id, "specification_hash": digest, "created": True}

    def record(self, *, experiment_id: str, hypothesis_id: str, dataset_id: str, config_hash: str,
               source: str, symbol: str, timeframe: str, metrics: Dict[str, Any],
               p_value: Optional[float], status="COMPLETED", run_id=None,
               sample_start=None, sample_end=None):
        required = {
            "experiment_id": experiment_id, "hypothesis_id": hypothesis_id,
            "dataset_id": dataset_id, "config_hash": config_hash,
            "source": source, "symbol": symbol, "timeframe": timeframe, "status": status,
        }
        for field, value in required.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
            if len(value) > 256:
                raise ValueError(f"{field} exceeds its length limit")
        if run_id is not None and (not isinstance(run_id, str) or len(run_id) > 128):
            raise ValueError("run_id must be a string of at most 128 characters")
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be an object")
        normalized_p = None if p_value is None else float(p_value)
        if normalized_p is not None and (not math.isfinite(normalized_p) or not 0 <= normalized_p <= 1):
            raise ValueError("p_value must be finite and in [0,1]")
        normalized_samples = []
        for field, value in (("sample_start", sample_start), ("sample_end", sample_end)):
            if value is None:
                normalized_samples.append(None)
                continue
            if isinstance(value, bool):
                raise ValueError(f"{field} must be finite numeric data")
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{field} must be finite numeric data") from exc
            if not math.isfinite(numeric):
                raise ValueError(f"{field} must be finite numeric data")
            normalized_samples.append(numeric)
        sample_start, sample_end = normalized_samples
        if sample_start is not None and sample_end is not None and sample_end < sample_start:
            raise ValueError("sample_end must be >= sample_start")
        payload = json.dumps(_safe_json(metrics), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > MAX_METRICS_BYTES:
            raise ValueError(f"experiment metrics exceed the {MAX_METRICS_BYTES // 1024} KiB limit")
        with self._db() as c:
            if not c.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone():
                raise ValueError("hypothesis must be registered before experiment")
            existing = c.execute(
                "SELECT hypothesis_id,dataset_id,config_hash,source,symbol,timeframe,run_id,"
                "sample_start,sample_end,metrics_json,p_value,status FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            candidate = (
                hypothesis_id, dataset_id, config_hash, source, symbol, timeframe, run_id,
                sample_start, sample_end, payload, normalized_p, status,
            )
            if existing:
                stored = tuple(existing[key] for key in (
                    "hypothesis_id", "dataset_id", "config_hash", "source", "symbol",
                    "timeframe", "run_id", "sample_start", "sample_end", "metrics_json",
                    "p_value", "status",
                ))
                if stored != candidate:
                    raise ValueError(
                        "Experiment ID is immutable; use a new ID for changed dataset, config, metrics or provenance"
                    )
                return {"experiment_id": experiment_id, "created": False}
            c.execute("""INSERT INTO experiments(
              experiment_id,hypothesis_id,dataset_id,config_hash,source,symbol,timeframe,run_id,
              sample_start,sample_end,metrics_json,p_value,adjusted_p_value,status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
              experiment_id,hypothesis_id,dataset_id,config_hash,source,symbol,timeframe,run_id,
              sample_start,sample_end,payload,normalized_p,None,status,time.time()))
        return {"experiment_id": experiment_id, "created": True}

    def apply_fdr(self, family_id="default"):
        """Benjamini-Hochberg across all completed experiments in a family."""
        with self._db() as c:
            rows = c.execute("""SELECT e.experiment_id,e.p_value FROM experiments e
                JOIN hypotheses h ON h.hypothesis_id=e.hypothesis_id
                WHERE e.status='COMPLETED' AND h.family_id=? AND e.p_value IS NOT NULL
                ORDER BY e.p_value ASC""", (family_id,)).fetchall()
            m = len(rows)
            adjusted = [1.0] * m
            running = 1.0
            for i in range(m - 1, -1, -1):
                raw = float(rows[i]["p_value"]) * m / (i + 1)
                running = min(running, raw)
                adjusted[i] = min(1.0, running)
            for row, q in zip(rows, adjusted):
                c.execute("UPDATE experiments SET adjusted_p_value=? WHERE experiment_id=?", (q, row["experiment_id"]))
        return {"family_id": family_id, "tests_counted": m, "method": "Benjamini-Hochberg FDR", "updated": m}

    def summary(self):
        with self._db() as c:
            h = c.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
            e = c.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            completed = c.execute("SELECT COUNT(*) FROM experiments WHERE status='COMPLETED'").fetchone()[0]
            failed = c.execute("SELECT COUNT(*) FROM experiments WHERE status!='COMPLETED'").fetchone()[0]
            pvalue_tests = c.execute("SELECT COUNT(*) FROM experiments WHERE status='COMPLETED' AND p_value IS NOT NULL").fetchone()[0]
            rows = c.execute("""SELECT e.*,h.name,h.family_id FROM experiments e JOIN hypotheses h
                ON h.hypothesis_id=e.hypothesis_id ORDER BY e.created_at DESC LIMIT 50""").fetchall()
        latest = []
        for r in rows:
            d = dict(r); d["metrics"] = json.loads(d.pop("metrics_json")); latest.append(d)
        return {"schema_version": SCHEMA_VERSION, "hypotheses": h, "experiments": e,
                "completed_experiments": completed, "failed_experiments": failed,
                "tests_counted": pvalue_tests, "recent_experiments": latest}

    def latest_sample_end(self, *, source, symbol, timeframe):
        with self._db() as c:
            row=c.execute("SELECT MAX(sample_end) FROM experiments WHERE source=? AND UPPER(symbol)=UPPER(?) AND timeframe=?",
                          (str(source),str(symbol),str(timeframe))).fetchone()
        return row[0] if row and row[0] is not None else None

    def list_hypotheses(self, limit=200):
        with self._db() as c:
            rows = c.execute("SELECT * FROM hypotheses ORDER BY registered_at DESC LIMIT ?", (max(1,min(int(limit),1000)),)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["specification"]=json.loads(d.pop("specification_json")); out.append(d)
        return out
