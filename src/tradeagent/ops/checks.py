"""Boot-time exposure checks (ADR-0022). Both halt the worker; neither is skippable when SUPABASE_URL is set."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

CONTEXT_BUCKET = "decision-context"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_data_api_exposure(http: httpx.Client, supabase_url: str, anon_key: str) -> CheckResult:
    """The trading schema must be unreachable through PostgREST with the anon key: 401/404/406 pass, 200 halts."""
    r = http.get(
        f"{supabase_url.rstrip('/')}/rest/v1/decisions",
        params={"select": "decision_id", "limit": "1"},
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
    )
    if r.status_code == 200:
        return CheckResult(
            "SUPABASE_EXPOSURE_CHECK",
            False,
            "trading.decisions is readable with the anon key: schema `trading` is exposed or grants leaked",
        )
    return CheckResult("SUPABASE_EXPOSURE_CHECK", True, f"anon access refused with HTTP {r.status_code}")


def ensure_private_bucket(
    http: httpx.Client, supabase_url: str, service_role_key: str, bucket: str = CONTEXT_BUCKET
) -> CheckResult:
    """Create the context bucket private if missing; halt if it exists and is public."""
    base = f"{supabase_url.rstrip('/')}/storage/v1/bucket"
    headers = {"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}"}
    r = http.get(f"{base}/{bucket}", headers=headers)
    if r.status_code == 404:
        c = http.post(base, json={"id": bucket, "name": bucket, "public": False}, headers=headers)
        if c.status_code >= 300:
            return CheckResult("STORAGE_BUCKET_CHECK", False, f"could not create bucket {bucket}: HTTP {c.status_code}")
        return CheckResult("STORAGE_BUCKET_CHECK", True, f"created private bucket {bucket}")
    if r.status_code != 200:
        return CheckResult("STORAGE_BUCKET_CHECK", False, f"bucket lookup failed: HTTP {r.status_code}")
    if bool(r.json().get("public")):
        return CheckResult(
            "STORAGE_BUCKET_CHECK", False, f"bucket {bucket} is PUBLIC; must be private (§10.4, ADR-0022)"
        )
    return CheckResult("STORAGE_BUCKET_CHECK", True, f"bucket {bucket} is private")
