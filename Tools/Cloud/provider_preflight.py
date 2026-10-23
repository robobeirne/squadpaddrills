#!/usr/bin/env python3
"""Read the public provider catalogue using the saved cleanup credential.

Only GET requests to sizes, regions and the public Ubuntu image are permitted.
This does not prove account creation eligibility, deletion scope or live cleanup.
No resource is created, modified or deleted. No account/resource data is fetched.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ORIGIN = "https://api.digitalocean.com"
SIZE = "s-4vcpu-8gb"
REGION = "lon1"
IMAGE = "ubuntu-22-04-x64"
MAX_BODY = 2_000_000


class CheckError(Exception):
    """Internal fixed reason codes only; never include provider responses."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def allowed_path(path):
    return isinstance(path, str) and (
        path == f"/v2/images/{IMAGE}" or
        re.fullmatch(r"/v2/(sizes|regions)\?per_page=200&page=(?:[1-9]|10)", path) is not None
    )


class Catalogue:
    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,512}", token):
            raise CheckError("cleanup_token_missing_or_invalid")
        self._token = token
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(self, path):
        if not allowed_path(path):
            raise CheckError("request_not_allowlisted")
        request = Request(ORIGIN + path, method="GET", headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json", "User-Agent": "SquadPad-ReadOnly-Preflight/1"
        })
        try:
            with self._opener.open(request, timeout=15) as response:
                if response.status != 200:
                    raise CheckError("provider_response_rejected")
                body = response.read(MAX_BODY + 1)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (401, 403):
                raise CheckError("provider_auth_or_read_scope_rejected") from None
            raise CheckError("provider_http_error") from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise CheckError("provider_network_error") from None
        if len(body) > MAX_BODY:
            raise CheckError("provider_response_too_large")
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise CheckError("provider_response_invalid") from None
        if not isinstance(data, dict):
            raise CheckError("provider_response_invalid")
        return data

    def find(self, collection, slug):
        if collection not in ("sizes", "regions"):
            raise CheckError("collection_not_allowlisted")
        for page in range(1, 11):
            data = self.get(f"/v2/{collection}?per_page=200&page={page}")
            rows = data.get(collection)
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise CheckError("provider_collection_invalid")
            matches = [row for row in rows if row.get("slug") == slug]
            if len(matches) > 1:
                raise CheckError("ambiguous_catalogue_entry")
            if matches:
                return matches[0]
            # Never follow a provider-supplied URL with the credential.
            links = data.get("links", {})
            pages = links.get("pages", {}) if isinstance(links, dict) else {}
            if not isinstance(pages, dict) or not pages.get("next"):
                break
        raise CheckError("required_catalogue_entry_missing")


def evaluate(client):
    size = client.find("sizes", SIZE)
    region = client.find("regions", REGION)
    image = client.get(f"/v2/images/{IMAGE}").get("image")
    if size.get("slug") != SIZE or any(
        type(size.get(k)) is not int or size[k] != v
        for k, v in {"vcpus": 4, "memory": 8192, "disk": 160}.items()
    ):
        raise CheckError("size_does_not_match_approved_configuration")
    try:
        price = Decimal(str(size.get("price_hourly")))
    except InvalidOperation:
        raise CheckError("invalid_catalogue_price") from None
    if not price.is_finite() or not Decimal("0") < price <= Decimal("0.072"):
        raise CheckError("price_exceeds_approved_hourly_ceiling")
    if (size.get("available") is not True or not isinstance(size.get("regions"), list)
            or REGION not in size["regions"] or region.get("slug") != REGION
            or region.get("available") is not True):
        raise CheckError("approved_plan_not_listed_available_in_london")
    if (not isinstance(image, dict) or image.get("slug") != IMAGE
            or image.get("public") is not True or image.get("status") != "available"
            or image.get("distribution") != "Ubuntu"
            or type(image.get("id")) is not int or image["id"] <= 0
            or not isinstance(image.get("regions"), list) or REGION not in image["regions"]):
        raise CheckError("public_ubuntu_image_not_available_in_london")
    return {"schema": "squadpad.provider-preflight.v1", "status": "passed",
            "catalogue_read_access_verified": True, "size_slug": SIZE, "region": REGION,
            "vcpus": 4, "memory_mib": 8192, "disk_gb": 160,
            "hourly_usd_before_tax": str(price), "public_image_id": image["id"],
            "public_image_slug": IMAGE, "account_creation_eligibility_verified": False,
            "deletion_permission_verified": False, "cleanup_armed": False,
            "resources_created": 0, "resources_deleted": 0}


def main():
    try:
        result = evaluate(Catalogue(os.environ.get("SQUADPAD_DO_TOKEN", "")))
        exit_code = 0
    except CheckError as exc:
        result = {"schema": "squadpad.provider-preflight.v1", "status": "failed", "reason": str(exc),
                  "cleanup_armed": False, "resources_created": 0, "resources_deleted": 0}
        exit_code = 1
    except Exception:
        # No traceback: provider content and credential-bearing objects stay private.
        result = {"schema": "squadpad.provider-preflight.v1", "status": "failed",
                  "reason": "unexpected_preflight_error", "cleanup_armed": False,
                  "resources_created": 0, "resources_deleted": 0}
        exit_code = 1
    text = json.dumps(result, indent=2)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("## Read-only DigitalOcean catalogue check\n\n```json\n" + text + "\n```\n\n")
            output.write("No resources were created, modified or deleted. Catalogue availability does not prove account eligibility or reserve capacity. Cleanup is not armed.\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
