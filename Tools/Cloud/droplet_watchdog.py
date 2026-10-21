#!/usr/bin/env python3
"""Independent, bounded cleanup guard for one newly created SquadPad Droplet.

Python 3.10+, stdlib only. Default is local preflight: no token access or API.
Use --watch on an already-running controller OUTSIDE the VM; observe its armed
receipt before creating the uniquely named machine. This never creates a VM.

The first attempt uses regular Basic 4 vCPU / 8 GiB RAM / 160 GiB disk in LON1.
The operator must supply a live-verified size slug and Ubuntu 22.04 image ID.
Keep a token with droplet:read and droplet:delete only in SQUADPAD_DO_TOKEN.
Scopes are an operator prerequisite, not something this program can prove.
API: https://docs.digitalocean.com/products/droplets/reference/api/droplets/

Verified disappearance is an operational result, NOT a guaranteed billing cap.
Controller cancellation, network/provider failure and account-level extras need
operator intervention. Image/volume visibility can require additional scopes;
missing fields are recorded as unavailable, never asserted to be empty.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import signal
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


API = "https://api.digitalocean.com"
TOKEN_ENV = "SQUADPAD_DO_TOKEN"
CREATE_WINDOW = 900
DELETE_AGE = 17_100
VERIFY_AGE = 18_000
HTTP_TIMEOUT = 15
MAX_BODY_BYTES = 2_000_000
NAME_PATTERN = r"squadpad-native-[a-z0-9][a-z0-9-]{6,38}[a-z0-9]"
EXPECTED = {"region": "lon1", "vcpus": 4, "memory": 8192, "disk": 160,
            "hourly_usd_ceiling": "0.072", "backups_enabled": False}


class GuardError(Exception):
    """Only fixed, non-sensitive reason codes may be supplied."""


class TransientError(Exception):
    pass


class Clock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)
    time = staticmethod(time.time)


@dataclass(frozen=True)
class Config:
    name: str
    size: str
    image_id: int

    def validate(self):
        if not re.fullmatch(NAME_PATTERN, self.name):
            raise GuardError("invalid_unique_name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", self.size):
            raise GuardError("invalid_size_slug")
        if type(self.image_id) is not int or self.image_id <= 0:
            raise GuardError("invalid_image_id")

    def expected(self):
        return dict(EXPECTED, name=self.name, size_slug=self.size, image_id=self.image_id)


@dataclass(frozen=True)
class Reply:
    status: int
    document: object = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError()


class HttpTransport:
    """Fixed HTTPS origin, no redirects/proxies, no raw body/error logging."""

    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,512}", token):
            raise GuardError("missing_or_invalid_token")
        self._token = token
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path):
        list_path = re.fullmatch(r"/v2/droplets\?name=[a-z0-9-]+&per_page=200&page=1", path)
        id_path = re.fullmatch(r"/v2/droplets/[1-9][0-9]*", path)
        if not ((method == "GET" and (list_path or id_path)) or
                (method == "DELETE" and id_path)):
            raise GuardError("request_not_allowlisted")
        request = Request(API + path, method=method, headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json", "User-Agent": "SquadPad-Droplet-Watchdog/1"})
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT) as response:
                status = response.status
                body = response.read(MAX_BODY_BYTES + 1) if status == 200 else b""
        except HTTPError as error:
            status = error.code
            error.close()  # Never consume or log the provider's error body.
            return Reply(status)
        except (URLError, OSError, TimeoutError, HTTPException):
            raise TransientError() from None
        if len(body) > MAX_BODY_BYTES:
            raise GuardError("provider_response_too_large")
        if status != 200:
            return Reply(status)
        try:
            document = json.loads(body, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, UnicodeError):
            raise GuardError("invalid_provider_json") from None
        return Reply(status, document)


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def provider_time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise GuardError("invalid_provider_created_at")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise GuardError("invalid_provider_created_at") from None


def initial_receipt(config, watch):
    config.validate()
    return {"schema": "squadpad.droplet-watchdog.v1", "mode": "watch" if watch else "preflight",
            "status": "starting" if watch else "preflight_only", "expected": config.expected(),
            "token_env": TOKEN_ENV, "required_token_scopes": ["droplet:read", "droplet:delete"],
            "watchdog_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "creation_window_seconds": CREATE_WINDOW, "delete_age_seconds": DELETE_AGE,
            "verify_before_age_seconds": VERIFY_AGE, "provider_api_contacted": False,
            "armed": False, "bound": None, "deletion_verified": False,
            "deletion_before_deadline": False, "billing_cap_guaranteed": False,
            "requests": 0, "delete_attempts": 0, "transient_errors": 0}


class Watchdog:
    def __init__(self, config, transport, *, clock=None, emit=None):
        self.config, self.transport = config, transport
        self.clock, self.emit = clock or Clock(), emit or (lambda _: None)
        self.receipt = initial_receipt(config, True)

    def update(self, status, **fields):
        self.receipt.update(status=status, **fields)
        self.emit(self.receipt)

    def call(self, method, path):
        self.receipt["provider_api_contacted"] = True
        self.receipt["requests"] += 1
        try:
            reply = self.transport.request(method, path)
        except TransientError:
            self.receipt["transient_errors"] += 1
            raise
        self.receipt["last_http_status"] = reply.status
        if reply.status in (401, 403):
            raise GuardError("provider_authentication_or_scope_failure")
        if reply.status in (409, 429) or 500 <= reply.status <= 599:
            self.receipt["transient_errors"] += 1
            raise TransientError()
        if reply.status not in (200, 204, 404):
            raise GuardError("unexpected_provider_http_status")
        return reply

    def candidate(self):
        query = urlencode({"name": self.config.name, "per_page": 200, "page": 1})
        reply = self.call("GET", "/v2/droplets?" + query)
        body = reply.document
        if reply.status != 200 or not isinstance(body, dict):
            raise GuardError("invalid_list_response")
        droplets, meta, links = body.get("droplets"), body.get("meta"), body.get("links", {})
        if not isinstance(droplets, list) or not isinstance(meta, dict) or not isinstance(links, dict):
            raise GuardError("invalid_list_response")
        total, pages = meta.get("total"), links.get("pages", {})
        if type(total) is not int or total < 0 or not isinstance(pages, dict):
            raise GuardError("invalid_list_response")
        # Only a complete zero/one-result set is usable. No pagination URL is followed.
        if total > 1 or len(droplets) > 1:
            raise GuardError("ambiguous_name_matches")
        if total != len(droplets) or pages.get("next"):
            raise GuardError("incomplete_list_response")
        for droplet in droplets:
            if not isinstance(droplet, dict) or str(droplet.get("name")).casefold() != self.config.name:
                raise GuardError("name_filter_not_honored")
        return droplets[0] if droplets else None

    def elapsed_epoch(self):
        # Never let a wall-clock rollback extend the provider-age budget.
        return max(self.clock.time(), self.arm_wall + self.clock.monotonic() - self.arm_mono)

    def bind(self, droplet):
        created = provider_time(droplet.get("created_at"))
        identity = droplet.get("id")
        if type(identity) is not int or identity <= 0:
            raise GuardError("invalid_provider_id")
        if created < math.floor(self.arm_wall) or created > self.elapsed_epoch() + 5:
            raise GuardError("droplet_not_created_after_arm")
        core = {"name": self.config.name, "size_slug": self.config.size,
                "vcpus": EXPECTED["vcpus"], "memory": EXPECTED["memory"], "disk": EXPECTED["disk"]}
        if any(droplet.get(key) != value or type(droplet.get(key)) is not type(value)
               for key, value in core.items()):
            raise GuardError("unexpected_droplet_configuration")
        region, image, size, features = (droplet.get(k) for k in ("region", "image", "size", "features"))
        if not all(isinstance(v, dict) for v in (region, image, size)) or not isinstance(features, list):
            raise GuardError("missing_configuration_evidence")
        if (region.get("slug") != EXPECTED["region"] or type(image.get("id")) is not int or
                image["id"] != self.config.image_id or size.get("slug") != self.config.size or
                "backups" in features or any(not isinstance(v, str) for v in features)):
            raise GuardError("unexpected_droplet_configuration")
        try:
            price = Decimal(str(size.get("price_hourly")))
        except InvalidOperation:
            raise GuardError("invalid_hourly_price") from None
        if not price.is_finite() or not Decimal(0) < price <= Decimal(EXPECTED["hourly_usd_ceiling"]):
            raise GuardError("hourly_price_above_ceiling_or_invalid")
        visibility = {}
        for field in ("backup_ids", "snapshot_ids", "volume_ids"):
            visibility[field] = "unavailable" if field not in droplet else "observed_empty"
            if field in droplet and droplet[field] != []:
                raise GuardError("unexpected_extra_resources")
        self.created = created
        self.path = "/v2/droplets/" + str(identity)
        self.update("bound", bound={"id": identity, "created_at": droplet["created_at"],
                    "configuration_verified": True, "extra_resource_visibility": visibility,
                    "observed_hourly_usd": str(price)},
                    delete_at_utc=utc(created + DELETE_AGE), verify_before_utc=utc(created + VERIFY_AGE))

    def check_absence(self):
        reply = self.call("GET", self.path)
        if reply.status == 404:
            age = self.elapsed_epoch() - self.created
            self.receipt.update(deletion_verified=True, verified_absent_at_utc=utc(self.clock.time()),
                                verified_absent_age_seconds=age, deletion_before_deadline=age < VERIFY_AGE)
            if age >= VERIFY_AGE:
                raise GuardError("absence_verified_after_deadline")
            self.update("deleted_verified")
            return True
        body = reply.document
        droplet = body.get("droplet") if isinstance(body, dict) else None
        if reply.status != 200 or not isinstance(droplet, dict):
            raise GuardError("invalid_get_response")
        bound = self.receipt["bound"]
        if (type(droplet.get("id")) is not int or droplet["id"] != bound["id"] or
                droplet.get("created_at") != bound["created_at"]):
            raise GuardError("bound_identity_changed")
        return False

    def run(self):
        try:
            if self.candidate() is not None:
                raise GuardError("planned_name_already_exists")
            self.arm_wall, self.arm_mono = self.clock.time(), self.clock.monotonic()
            self.update("armed", armed=True, armed_at_utc=utc(self.arm_wall))
            while True:
                remaining = CREATE_WINDOW - (self.clock.monotonic() - self.arm_mono)
                if remaining <= 0:
                    raise GuardError("creation_window_expired")
                self.clock.sleep(min(15, remaining))
                try:
                    droplet = self.candidate()
                except TransientError:
                    continue
                if self.clock.monotonic() - self.arm_mono >= CREATE_WINDOW:
                    raise GuardError("creation_window_expired")
                if droplet is not None:
                    self.bind(droplet)
                    break
            while True:
                age = self.elapsed_epoch() - self.created
                if age >= VERIFY_AGE:
                    raise GuardError("deletion_verification_deadline_expired")
                if age >= DELETE_AGE:
                    self.update("deleting")
                    self.receipt["delete_attempts"] += 1
                    try:
                        reply = self.call("DELETE", self.path)
                        if reply.status not in (204, 404):
                            raise GuardError("invalid_delete_response")
                    except TransientError:
                        pass  # The delete may have arrived; only a fresh GET can settle it.
                transient = False
                try:
                    if self.check_absence():
                        return self.receipt
                except TransientError:
                    transient = True
                age = self.elapsed_epoch() - self.created
                boundary = DELETE_AGE if age < DELETE_AGE else VERIFY_AGE
                self.clock.sleep(max(0, min(30 if transient or age >= DELETE_AGE else 300, boundary - age)))
        except (KeyboardInterrupt, GuardError, TransientError) as error:
            reason = ("interrupted" if isinstance(error, KeyboardInterrupt) else
                      "initial_request_failed" if isinstance(error, TransientError) else str(error))
            self.update("failed", failure_reason=reason)
            return self.receipt


class ReceiptSink:
    def __init__(self, path):
        self.path, self.fresh = path, True

    def __call__(self, receipt):
        content = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
        if self.path:
            if self.fresh:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("x", encoding="utf-8") as handle:
                    handle.write(content)
                self.fresh = False
            else:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix="watchdog-", delete=False) as handle:
                    handle.write(content)
                    temporary = handle.name
                os.replace(temporary, self.path)
        print(content, end="", flush=True)


def interrupt(*_):
    raise KeyboardInterrupt()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Unique squadpad-native- name; suffix 8–40 characters")
    parser.add_argument("--size", required=True, help="Size slug verified from the live provider catalogue")
    parser.add_argument("--image-id", required=True, type=int, help="Verified Ubuntu 22.04 x86-64 image ID")
    parser.add_argument("--watch", action="store_true", help="Contact API and supervise only this fresh planned VM")
    parser.add_argument("--receipt", type=Path, help="New receipt file; existing files are rejected")
    args = parser.parse_args(argv)
    handlers, prepared = {}, False
    try:
        config = Config(args.name, args.size, args.image_id)
        receipt = initial_receipt(config, args.watch)
        sink = ReceiptSink(args.receipt)
        sink(receipt)  # Refuse stale output before accessing the token or provider.
        prepared = True
        if not args.watch:
            return 0
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None:
                handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupt)
        transport = HttpTransport(os.environ.get(TOKEN_ENV))
        watchdog = Watchdog(config, transport, emit=sink)
        receipt = watchdog.receipt  # Retain bound identity if local output later fails.
        watchdog.run()
        return 0 if receipt["status"] == "deleted_verified" else 1
    except (GuardError, OSError, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, GuardError) else "interrupted_or_local_io_failure"
        # Do not expose raw OS/provider messages: they can contain paths or credentials.
        failure = {"status": "failed", "failure_reason": reason,
                   "deletion_verified": False, "billing_cap_guaranteed": False}
        if prepared:
            receipt.update(failure)
            try:
                sink(receipt)
            except OSError:
                print(json.dumps(failure), flush=True)
        else:
            print(json.dumps(failure), flush=True)
        return 1
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
