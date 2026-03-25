import base64
import hashlib
import logging
import os
import uuid
import time
from datetime import datetime, timezone
from fastapi import HTTPException
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from azure.core.pipeline.transport import RequestsTransport
from config import (
    LIVENESS_API_KEY,
    LIVENESS_ENDPOINT,
    LIVENESS_THRESHOLD,
    LIVENESS_TENANT_THRESHOLDS,
)

try:
    from azure.ai.vision.face import FaceSessionClient
except Exception:  # pragma: no cover
    FaceSessionClient = None

logger = logging.getLogger("faceauth")

LIVENESS_CONNECT_TIMEOUT_SEC = int(os.getenv("LIVENESS_CONNECT_TIMEOUT_SEC", "30"))
LIVENESS_READ_TIMEOUT_SEC = int(os.getenv("LIVENESS_READ_TIMEOUT_SEC", "90"))
LIVENESS_CREATE_ATTEMPTS = max(1, int(os.getenv("LIVENESS_CREATE_ATTEMPTS", "2")))
LIVENESS_AUTH_TOKEN_TTL_SEC = max(120, int(os.getenv("LIVENESS_AUTH_TOKEN_TTL_SEC", "300")))


def _raise_mapped_azure_error(exc: Exception) -> None:
    text = str(exc).lower()
    if isinstance(exc, ServiceRequestError):
        message = str(exc)
        if "timed out" in text or "timeout" in text:
            raise HTTPException(
                status_code=504,
                detail=(
                    "Liveness endpoint timed out while contacting Azure Face service. "
                    "Please retry; if this persists, check firewall/proxy outbound access to the liveness endpoint."
                ),
            )
        raise HTTPException(status_code=502, detail=f"Liveness network error: {message}")

    if "timed out" in text or "timeout" in text:
        raise HTTPException(
            status_code=504,
            detail=(
                "Liveness endpoint timed out while contacting Azure Face service. "
                "Please retry; if this persists, check firewall/proxy outbound access to the liveness endpoint."
            ),
        )

    if isinstance(exc, HttpResponseError):
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "error", None)
        code_val = getattr(code, "code", None) if code is not None else None
        message = str(exc)

        text = (f"{code_val or ''} {message}").lower()
        if status == 404 or "sessionnotfound" in text:
            raise HTTPException(status_code=404, detail="Liveness session was not found or has expired.")
        if status == 401 or status == 403:
            raise HTTPException(status_code=500, detail="Liveness credentials are invalid or unauthorized.")
        if "invalidimagesize" in text or "invalidimage" in text:
            raise HTTPException(status_code=400, detail="Invalid liveness image. Ensure a clear JPEG/PNG with sufficient size.")
        if status == 400:
            raise HTTPException(status_code=400, detail=f"Invalid liveness request: {message}")
        raise HTTPException(status_code=502, detail=f"Azure liveness request failed: {message}")

    raise HTTPException(status_code=500, detail=f"Liveness operation failed: {str(exc)}")


def _require_liveness_client():
    if not LIVENESS_ENDPOINT or not LIVENESS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Liveness credentials are missing. Set LIVENESS_ENDPOINT and LIVENESS_API_KEY.",
        )
    if FaceSessionClient is None:
        raise HTTPException(
            status_code=500,
            detail="Liveness SDK not installed. Install azure-ai-vision-face package.",
        )


def _make_liveness_client():
    transport = RequestsTransport(
        connection_timeout=LIVENESS_CONNECT_TIMEOUT_SEC,
        read_timeout=LIVENESS_READ_TIMEOUT_SEC,
    )
    return FaceSessionClient(
        endpoint=LIVENESS_ENDPOINT,
        credential=AzureKeyCredential(LIVENESS_API_KEY),
        transport=transport,
    )


def _sleep_backoff(attempt_index: int) -> None:
    # Fast progressive delay to absorb transient endpoint/network blips.
    delays = [0.8, 1.6, 3.0, 5.0]
    if attempt_index < len(delays):
        time.sleep(delays[attempt_index])


def _decode_image(image_b64: str) -> bytes:
    try:
        data = image_b64.split(",")[1] if "," in image_b64 else image_b64
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be base64 encoded.")


def _safe_request_id() -> str:
    seed = str(uuid.uuid4())
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _mode_value(mode: str) -> str:
    m = (mode or "Passive").strip().lower()
    if m in ("passiveactive", "passive_active", "passive-active"):
        return "PassiveActive"
    return "Passive"


def _extract_liveness_confidence(result_obj) -> float:
    candidates = [
        getattr(result_obj, "liveness_confidence", None),
        getattr(result_obj, "liveness_score", None),
        getattr(result_obj, "confidence", None),
    ]
    for c in candidates:
        if c is not None:
            try:
                return float(c)
            except Exception:
                pass
    return 0.0


def _extract_session_status(result_obj) -> str:
    status = getattr(result_obj, "status", None)
    return str(status).strip() if status is not None else ""


def _is_result_available_status(status: str) -> bool:
    if not status:
        return False
    s = str(status).strip().lower()
    if "." in s:
        s = s.split(".")[-1]
    s = s.replace("_", "").replace(" ", "")
    return s == "resultavailable"


def _extract_response_body(result_obj):
    audit = getattr(result_obj, "result", None)
    if audit is None:
        return None
    response = getattr(audit, "response", None)
    if response is None:
        return None
    return getattr(response, "body", None)


def _extract_session_image_id(result_obj) -> str | None:
    audit = getattr(result_obj, "result", None)
    if audit is None:
        return None
    sid = getattr(audit, "session_image_id", None)
    if sid is None:
        return None
    value = str(sid).strip()
    return value or None


def _decision_to_score(decision: str) -> float:
    d = (decision or "").strip().lower()
    if "." in d:
        d = d.split(".")[-1]
    d = d.replace("_", "").replace("-", "").replace(" ", "")
    if d == "realface":
        return 1.0
    if d == "spoofface":
        return 0.0
    return 0.5


def _normalize_liveness_decision(decision: str) -> str:
    d = (decision or "").strip().lower()
    if "." in d:
        d = d.split(".")[-1]
    d = d.replace("_", "").replace("-", "").replace(" ", "")
    if d == "realface":
        return "realface"
    if d == "spoofface":
        return "spoofface"
    if d == "uncertain":
        return "uncertain"
    return "uncertain"


def _extract_spoof_type(result_obj) -> str | None:
    for attr in ("spoof_type", "presentation_attack_type", "attack_type"):
        value = getattr(result_obj, attr, None)
        if value is not None:
            v = str(value).strip()
            if v:
                return v
    return None


def _extract_is_live(result_obj) -> bool:
    candidates = [
        getattr(result_obj, "is_live", None),
        getattr(result_obj, "isLive", None),
        getattr(result_obj, "is_liveness_passed", None),
    ]
    for c in candidates:
        if isinstance(c, bool):
            return c
    return False


def _resolve_threshold(tenant_id: str | None, explicit_threshold: float | None) -> float:
    if explicit_threshold is not None:
        return max(0.0, min(1.0, float(explicit_threshold)))
    if tenant_id:
        t = LIVENESS_TENANT_THRESHOLDS.get(str(tenant_id))
        if t is not None:
            return max(0.0, min(1.0, float(t)))
    return max(0.0, min(1.0, float(LIVENESS_THRESHOLD)))


def _build_liveness_decision(result_obj, session_id: str, tenant_id: str | None, tenant_threshold: float | None) -> dict:
    status = _extract_session_status(result_obj)
    threshold = _resolve_threshold(tenant_id, tenant_threshold)
    session_image_id = _extract_session_image_id(result_obj)

    # Session exists but capture flow has not completed on client yet.
    if status and not _is_result_available_status(status):
        req_id = _safe_request_id()
        response = {
            "requestId": req_id,
            "sessionId": session_id,
            "isLive": False,
            "livenessConfidence": 0.0,
            "decision": "inconclusive",
            "reason": "capture_not_completed",
            "threshold": threshold,
            "mode": "passive",
            "sessionStatus": status,
            "modelIsLive": None,
            "rawLivenessScore": 0.0,
        }
        if session_image_id:
            response["sessionImageId"] = session_image_id
        logger.info(
            "liveness_result_pending request_id=%s timestamp=%s session_status=%s decision=%s",
            req_id,
            datetime.now(timezone.utc).isoformat(),
            status,
            response["decision"],
        )
        return response

    body = _extract_response_body(result_obj)
    liveness_decision_raw = "uncertain"
    if body is not None:
        liveness_decision_raw = str(getattr(body, "liveness_decision", "uncertain") or "uncertain").strip().lower()

    liveness_decision = _normalize_liveness_decision(liveness_decision_raw)

    raw_score = _decision_to_score(liveness_decision)
    model_is_live = liveness_decision == "realface"

    # Decision confidence: confidence of final decision.
    decision_conf = raw_score if model_is_live else (1.0 - raw_score)

    spoof_type = _extract_spoof_type(result_obj)
    reason = None
    if liveness_decision == "spoofface":
        decision = "fail"
        reason = "spoof_detected"
        # AC2 asks for high-confidence spoof rejection; keep confidence at least threshold.
        decision_conf = max(decision_conf, threshold)
        is_live = False
    elif liveness_decision == "uncertain":
        decision = "inconclusive"
        reason = "liveness_uncertain"
        is_live = False
    elif raw_score < threshold:
        decision = "fail"
        reason = "liveness_below_threshold"
        is_live = False
    else:
        decision = "pass"
        reason = None
        is_live = True

    req_id = _safe_request_id()
    logger.info(
        "liveness_result request_id=%s timestamp=%s raw_score=%.4f decision_conf=%.4f model_is_live=%s decision=%s",
        req_id,
        datetime.now(timezone.utc).isoformat(),
        raw_score,
        decision_conf,
        model_is_live,
        decision,
    )

    response = {
        "requestId": req_id,
        "sessionId": session_id,
        "isLive": is_live,
        "livenessConfidence": round(float(decision_conf), 4),
        "decision": decision,
        "reason": reason,
        "threshold": threshold,
        "mode": "passive",
        "sessionStatus": status or "ResultAvailable",
        "modelIsLive": model_is_live,
        "rawLivenessScore": round(float(raw_score), 4),
        "rawLivenessDecision": liveness_decision_raw,
    }
    if session_image_id:
        response["sessionImageId"] = session_image_id
    if body is not None:
        verify_result = getattr(body, "verify_result", None)
        if verify_result is not None:
            vmc = getattr(verify_result, "match_confidence", None)
            if vmc is not None:
                try:
                    response["verifyMatchConfidence"] = round(float(vmc), 4)
                except Exception:
                    pass
    if spoof_type:
        response["spoofType"] = spoof_type
    return response


def get_session_image_bytes(session_image_id: str) -> bytes:
    """Fetch raw bytes for a liveness session image id."""
    _require_liveness_client()
    if not session_image_id or not str(session_image_id).strip():
        raise HTTPException(status_code=400, detail="session_image_id is required.")

    with _make_liveness_client() as client:
        try:
            stream = client.get_session_image(str(session_image_id).strip())
            data = b"".join(stream)
        except Exception as e:
            _raise_mapped_azure_error(e)

    if not data:
        raise HTTPException(status_code=404, detail="Session image was not found or empty.")
    return data


def create_liveness_session(liveness_operation_mode: str = "PassiveActive") -> dict:
    """Create passive/passive-active liveness session and return auth token payload."""
    _require_liveness_client()

    with _make_liveness_client() as client:
        last_error = None
        for attempt in range(LIVENESS_CREATE_ATTEMPTS):
            try:
                session_content = {
                    "livenessOperationMode": _mode_value(liveness_operation_mode),
                    "deviceCorrelationId": str(uuid.uuid4()),
                    "sendResultsToClient": True,
                    "authTokenTimeToLiveInSeconds": LIVENESS_AUTH_TOKEN_TTL_SEC,
                }
                created = client.create_liveness_session(session_content)
                break
            except Exception as e:
                last_error = e
                if attempt < (LIVENESS_CREATE_ATTEMPTS - 1):
                    logger.warning(
                        "create_liveness_session_attempt_failed attempt=%s/%s error=%s",
                        attempt + 1,
                        LIVENESS_CREATE_ATTEMPTS,
                        str(e),
                    )
                    _sleep_backoff(attempt)
                    continue
                _raise_mapped_azure_error(e)

        if last_error is not None and 'created' not in locals():
            _raise_mapped_azure_error(last_error)

    if not getattr(created, "auth_token", None):
        raise HTTPException(status_code=500, detail="No auth token received from Azure liveness API.")

    req_id = _safe_request_id()
    logger.info(
        "liveness_session_created request_id=%s timestamp=%s",
        req_id,
        datetime.now(timezone.utc).isoformat(),
    )

    return {
        "requestId": req_id,
        "sessionId": created.session_id,
        "session_id": created.session_id,
        "authToken": created.auth_token,
        "statusText": "Liveness session created successfully",
        "mode": liveness_operation_mode,
        "status": "created",
    }


def get_liveness_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
) -> dict:
    """Retrieve liveness result and map to AC-friendly response."""
    _require_liveness_client()

    with _make_liveness_client() as client:
        try:
            result = client.get_liveness_session_result(session_id)
        except Exception as e:
            _raise_mapped_azure_error(e)

    return _build_liveness_decision(result, session_id, tenant_id, tenant_threshold)


def create_liveness_with_verify_session(verify_image_b64: str, liveness_operation_mode: str = "PassiveActive") -> dict:
    """Create liveness+verify session using a provided verify image."""
    _require_liveness_client()
    verify_bytes = _decode_image(verify_image_b64)

    with _make_liveness_client() as client:
        last_error = None
        for attempt in range(LIVENESS_CREATE_ATTEMPTS):
            try:
                session_content = {
                    "liveness_operation_mode": _mode_value(liveness_operation_mode),
                    "device_correlation_id": str(uuid.uuid4()),
                    "send_results_to_client": True,
                    "auth_token_time_to_live_in_seconds": LIVENESS_AUTH_TOKEN_TTL_SEC,
                }
                created = client.create_liveness_with_verify_session(
                    session_content,
                    verify_image=verify_bytes,
                )
                break
            except Exception as e:
                last_error = e
                if attempt < (LIVENESS_CREATE_ATTEMPTS - 1):
                    logger.warning(
                        "create_liveness_with_verify_session_attempt_failed attempt=%s/%s error=%s",
                        attempt + 1,
                        LIVENESS_CREATE_ATTEMPTS,
                        str(e),
                    )
                    _sleep_backoff(attempt)
                    continue
                _raise_mapped_azure_error(e)

        if last_error is not None and 'created' not in locals():
            _raise_mapped_azure_error(last_error)

    if not getattr(created, "auth_token", None):
        raise HTTPException(status_code=500, detail="No auth token received from Azure liveness+verify API.")

    req_id = _safe_request_id()
    logger.info(
        "liveness_verify_session_created request_id=%s timestamp=%s",
        req_id,
        datetime.now(timezone.utc).isoformat(),
    )

    return {
        "requestId": req_id,
        "sessionId": created.session_id,
        "session_id": created.session_id,
        "authToken": created.auth_token,
        "statusText": "Liveness with verify session created successfully",
        "mode": liveness_operation_mode,
        "status": "created",
    }


def get_liveness_with_verify_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
) -> dict:
    """Retrieve liveness+verify session result and map to AC-friendly response."""
    _require_liveness_client()

    with _make_liveness_client() as client:
        try:
            result = client.get_liveness_with_verify_session_result(session_id)
        except Exception as e:
            _raise_mapped_azure_error(e)

    return _build_liveness_decision(result, session_id, tenant_id, tenant_threshold)


def create_liveness_verify_session_for_person(
    person_id: str,
    liveness_operation_mode: str = "PassiveActive",
) -> dict:
    """Create liveness+verify session using the enrolled reference image for a person_id.

    The reference image is stored in blob storage at enrollment time.
    This is the preferred session type — Azure runs liveness and similarity together,
    returning a single result with both decisions.
    """
    from services.blob_service import download_reference_image
    import base64 as _b64

    if not person_id or not person_id.strip():
        raise HTTPException(status_code=400, detail="person_id is required.")

    reference_bytes = download_reference_image(person_id.strip())
    reference_b64 = _b64.b64encode(reference_bytes).decode("utf-8")
    return create_liveness_with_verify_session(reference_b64, liveness_operation_mode)
