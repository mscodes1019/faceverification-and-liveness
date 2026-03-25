import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel, Field

from config import LIVENESS_THRESHOLD, VERIFY_PASS_THRESHOLD_PERCENT, VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT, VERIFY_TENANT_THRESHOLDS

router = APIRouter()


class EdgeResult(BaseModel):
    isLive: bool
    livenessConfidence: float = Field(ge=0, le=1)
    similarityPercent: float = Field(ge=0, le=100)
    modelVersion: str | None = None
    capturedAtUtc: str | None = None


class EdgeEvidence(BaseModel):
    probe_image_b64: str | None = None
    reference_hash: str | None = None


class EdgeVerifyRequest(BaseModel):
    device_id: str
    tenant_id: str | None = None
    person_id: str | None = None
    mode: str = "verify_1to1"
    edge_result: EdgeResult
    evidence: EdgeEvidence | None = None


class SyncEventResult(BaseModel):
    isLive: bool
    livenessConfidence: float = Field(ge=0, le=1)
    similarityPercent: float = Field(ge=0, le=100)
    matchStatus: str


class SyncEvent(BaseModel):
    event_id: str
    captured_at_utc: str
    mode: str
    tenant_id: str | None = None
    person_id: str | None = None
    result: SyncEventResult


class SyncEventsRequest(BaseModel):
    device_id: str
    events: list[SyncEvent]


_SYNC_EVENTS: dict[str, dict] = {}


def _resolve_thresholds_percent(tenant_id: str | None) -> tuple[float, float]:
    p = float(VERIFY_PASS_THRESHOLD_PERCENT)
    i = float(VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT)
    if tenant_id:
        cfg = VERIFY_TENANT_THRESHOLDS.get(str(tenant_id))
        if isinstance(cfg, dict):
            p = float(cfg.get("pass", p))
            i = float(cfg.get("inconclusive", i))
    if i > p:
        i = p
    return p, i


def _match_status_from_similarity(similarity_percent: float, pass_threshold: float, inconclusive_threshold: float) -> str:
    if similarity_percent >= pass_threshold:
        return "match"
    if similarity_percent >= inconclusive_threshold:
        return "inconclusive"
    return "no_match"


@router.post("/verify/edge")
async def verify_edge(req: EdgeVerifyRequest):
    """Edge compatibility endpoint: accept edge inference payload and apply central policy."""
    pass_t, inc_t = _resolve_thresholds_percent(req.tenant_id)

    if not req.edge_result.isLive or req.edge_result.livenessConfidence < float(LIVENESS_THRESHOLD):
        return {
            "status": "ok",
            "provider": "edge",
            "mode": req.mode,
            "tenant_id": req.tenant_id,
            "device_id": req.device_id,
            "person_id": req.person_id,
            "isLive": False,
            "livenessConfidence": req.edge_result.livenessConfidence,
            "similarityPercent": 0.0,
            "matchStatus": "inconclusive",
            "policyDecision": "deny",
            "reasons": ["liveness_not_passed"],
            "thresholds": {
                "pass": round(pass_t, 2),
                "inconclusive": round(inc_t, 2),
                "liveness": float(LIVENESS_THRESHOLD),
            },
            "modelVersion": req.edge_result.modelVersion,
        }

    match_status = _match_status_from_similarity(req.edge_result.similarityPercent, pass_t, inc_t)
    if match_status == "match":
        decision = "allow"
    elif match_status == "inconclusive":
        decision = "manual_review"
    else:
        decision = "deny"

    return {
        "status": "ok",
        "provider": "edge",
        "mode": req.mode,
        "tenant_id": req.tenant_id,
        "device_id": req.device_id,
        "person_id": req.person_id,
        "isLive": True,
        "livenessConfidence": req.edge_result.livenessConfidence,
        "similarityPercent": req.edge_result.similarityPercent,
        "matchStatus": match_status,
        "policyDecision": decision,
        "reasons": [],
        "thresholds": {
            "pass": round(pass_t, 2),
            "inconclusive": round(inc_t, 2),
            "liveness": float(LIVENESS_THRESHOLD),
        },
        "modelVersion": req.edge_result.modelVersion,
    }


@router.post("/sync/events")
async def sync_events(req: SyncEventsRequest):
    """Periodic edge-to-cloud sync endpoint with idempotency by event_id."""
    accepted: list[str] = []
    rejected: list[dict] = []

    for ev in req.events:
        if ev.event_id in _SYNC_EVENTS:
            rejected.append({"event_id": ev.event_id, "reason": "duplicate_event"})
            continue

        checksum = hashlib.sha256(f"{req.device_id}:{ev.event_id}:{ev.captured_at_utc}".encode("utf-8")).hexdigest()
        _SYNC_EVENTS[ev.event_id] = {
            "device_id": req.device_id,
            "tenant_id": ev.tenant_id,
            "person_id": ev.person_id,
            "mode": ev.mode,
            "captured_at_utc": ev.captured_at_utc,
            "result": ev.result.model_dump(),
            "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
            "checksum": checksum,
        }
        accepted.append(ev.event_id)

    return {
        "status": "ok",
        "device_id": req.device_id,
        "accepted": accepted,
        "rejected": rejected,
        "ingestedCount": len(accepted),
        "rejectedCount": len(rejected),
    }
