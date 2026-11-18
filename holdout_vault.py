"""One-way final holdout seal with single-use evaluation claim."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path

_LOCK=threading.RLock()

class HoldoutVault:
    def __init__(self,path="holdout_vault.json"):
        self.path=Path(path)
    def _read(self):
        if not self.path.exists(): return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"status": "CORRUPT"}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"status":"CORRUPT"}

    def _write_atomic(self, data):
        """Atomically persist vault metadata with owner-only permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            if os.name == "posix":
                os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    def status(self):
        d=self._read()
        if d is None: return {"status":"UNCONFIGURED","locked":True,"message":"No final holdout has been sealed."}
        if d.get("status")=="CORRUPT": return {"status":"CORRUPT","locked":True,"message":"Vault metadata unreadable; fail closed."}
        return {"status":d.get("status","SEALED"),"locked":True,"sealed_at":d.get("sealed_at"),
                "dataset_id":d.get("dataset_id"),"config_hash":d.get("config_hash"),
                "source":d.get("source"),"symbol":d.get("symbol"),"timeframe":d.get("timeframe"),
                "start_timestamp":d.get("start_timestamp"),"end_timestamp":d.get("end_timestamp"),
                "access_count":int(d.get("access_count",0)),
                "policy":"Final holdout is excluded from ordinary backtest/WF endpoints and may be evaluated once against the sealed strategy fingerprint."}
    def seal(self, *, dataset_id, config_hash, source, symbol, timeframe, start_timestamp, end_timestamp):
        fields = {"dataset_id": (dataset_id, 128), "config_hash": (config_hash, 128),
                  "source": (source, 32), "symbol": (symbol, 32), "timeframe": (timeframe, 16)}
        for name, (value, limit) in fields.items():
            text = str(value or "")
            if not text or len(text) > limit or any(ord(ch) < 32 for ch in text):
                raise ValueError(f"{name} is empty, too long or contains control characters")
        with _LOCK:
            if self.path.exists(): raise ValueError("Holdout already sealed; use a new future sample and a new vault file for a replacement")
            if (not dataset_id or not config_hash or not math.isfinite(float(start_timestamp))
                    or not math.isfinite(float(end_timestamp)) or end_timestamp<=start_timestamp):
                raise ValueError("dataset_id, config_hash and finite valid time range are required")
            d={"status":"SEALED","locked":True,"dataset_id":str(dataset_id),"config_hash":str(config_hash),
               "source":str(source),"symbol":str(symbol).upper(),"timeframe":str(timeframe),
               "start_timestamp":float(start_timestamp),"end_timestamp":float(end_timestamp),
               "sealed_at":time.time(),"access_count":0}
            self._write_atomic(d)
        return self.status()
    def overlaps(self, *, source, symbol, timeframe, start_timestamp, end_timestamp):
        d=self._read()
        if not d or d.get("status")=="CORRUPT": return bool(d)
        same=(d.get("source")==str(source) and str(d.get("symbol","")).upper()==str(symbol).upper()
              and d.get("timeframe")==str(timeframe))
        overlap=float(start_timestamp)<=float(d["end_timestamp"]) and float(end_timestamp)>=float(d["start_timestamp"])
        return bool(same and overlap)
    def claim(self, *, dataset_id, config_hash, source, symbol, timeframe, start_timestamp, end_timestamp):
        with _LOCK:
            d=self._read()
            if not isinstance(d,dict) or d.get("status")=="CORRUPT": raise ValueError("holdout vault is not in a valid sealed state")
            if int(d.get("access_count",0))>=1 or d.get("status")=="OPENED_ONCE": raise ValueError("final holdout has already been opened; it cannot be reused")
            if d.get("status")!="SEALED": raise ValueError("holdout vault is not in a valid sealed state")
            checks={"dataset_id":str(dataset_id)==d.get("dataset_id"),
                    "config_hash":str(config_hash)==d.get("config_hash"),
                    "source":str(source)==d.get("source"),
                    "symbol":str(symbol).upper()==str(d.get("symbol","")).upper(),
                    "timeframe":str(timeframe)==d.get("timeframe"),
                    "time_range":abs(float(start_timestamp)-float(d.get("start_timestamp")))<1e-6 and abs(float(end_timestamp)-float(d.get("end_timestamp")))<1e-6}
            failed=[k for k,v in checks.items() if not v]
            if failed: raise ValueError("holdout identity mismatch: "+", ".join(failed))
            d["access_count"]=1; d["last_access_at"]=time.time(); d["status"]="OPENED_ONCE"
            self._write_atomic(d)
            return {"access_count":1,"status":"OPENED_ONCE","checks":checks}
