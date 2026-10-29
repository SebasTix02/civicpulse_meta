"""Thin PostgREST client with the service_role key. The orchestrator is the only writer of
spec, intake, clusters, dispatches and stage_runs (API contract, orchestrator.openapi.yaml)."""

from __future__ import annotations

from typing import Any

import httpx


class Db:
    def __init__(self, url: str, service_key: str, client: httpx.Client | None = None):
        self.base = url.rstrip("/") + "/rest/v1"
        self.storage = url.rstrip("/") + "/storage/v1/object"
        self.client = client or httpx.Client(timeout=15.0)
        # New secret keys (sb_secret_...) are not JWTs and go only in `apikey`; the legacy
        # service_role JWT is also sent as Bearer, as PostgREST expected before.
        self.headers = {"apikey": service_key}
        if service_key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {service_key}"

    def select_one(self, table: str, **eq: Any) -> dict | None:
        params = {k: f"eq.{v}" for k, v in eq.items()} | {"limit": "1"}
        resp = self.client.get(f"{self.base}/{table}", params=params, headers=self.headers)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def rpc(self, fn: str, args: dict) -> Any:
        resp = self.client.post(f"{self.base}/rpc/{fn}", json=args, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def select(self, table: str, params: dict[str, str]) -> list[dict]:
        """Raw PostgREST filters, e.g. {"status": "in.(received,spec_ready)", "updated_at": "lt.<iso>"}."""
        resp = self.client.get(f"{self.base}/{table}", params=params, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def insert(self, table: str, row: dict) -> None:
        resp = self.client.post(f"{self.base}/{table}", json=row, headers=self.headers | {"Prefer": "return=minimal"})
        resp.raise_for_status()

    def update(self, table: str, patch: dict, **eq: Any) -> None:
        params = {k: f"eq.{v}" for k, v in eq.items()}
        resp = self.client.patch(f"{self.base}/{table}", params=params, json=patch,
                                 headers=self.headers | {"Prefer": "return=minimal"})
        resp.raise_for_status()

    def download(self, path: str) -> bytes:
        """`path` is bucket/name, as photos.storage_path stores it (raw/... or sandbox/...)."""
        resp = self.client.get(f"{self.storage}/{path}", headers=self.headers)
        resp.raise_for_status()
        return resp.content

    def upload(self, path: str, data: bytes, content_type: str = "image/jpeg") -> None:
        """Writes bucket/name, replacing it: re-running a stage rewrites the same file."""
        resp = self.client.post(f"{self.storage}/{path}", content=data,
                                headers=self.headers | {"Content-Type": content_type, "x-upsert": "true"})
        resp.raise_for_status()

    def update_where(self, table: str, patch: dict, params: dict[str, str]) -> bool:
        """update ... where <raw PostgREST filters>. False if no row matched."""
        resp = self.client.patch(f"{self.base}/{table}", params=params, json=patch,
                                 headers=self.headers | {"Prefer": "return=representation"})
        resp.raise_for_status()
        return bool(resp.json())

    def compare_and_set(self, table: str, id_: str, expected_status: str, patch: dict) -> bool:
        """update ... where id = :id and status = :expected. False if another worker moved it."""
        resp = self.client.patch(
            f"{self.base}/{table}",
            params={"id": f"eq.{id_}", "status": f"eq.{expected_status}"},
            json=patch,
            headers=self.headers | {"Prefer": "return=representation"},
        )
        resp.raise_for_status()
        return bool(resp.json())

    def stage_done(self, stage: str, *, report_id: str | None = None, photo_version: int | None = None,
                   resolution_id: str | None = None) -> bool:
        params = {"stage": f"eq.{stage}", "status": "eq.ok", "limit": "1"}
        if report_id:
            params |= {"report_id": f"eq.{report_id}", "photo_version": f"eq.{photo_version}"}
        else:
            params |= {"resolution_id": f"eq.{resolution_id}"}
        resp = self.client.get(f"{self.base}/stage_runs", params=params, headers=self.headers)
        resp.raise_for_status()
        return bool(resp.json())
