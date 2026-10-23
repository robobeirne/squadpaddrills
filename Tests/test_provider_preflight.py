import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError
from Tools.Cloud import provider_preflight as p


class FakeCatalogue:
    def __init__(self):
        self.size = dict(slug=p.SIZE, vcpus=4, memory=8192, disk=160, price_hourly="0.07143",
                         available=True, regions=["lon1"])
        self.region = dict(slug="lon1", available=True)
        self.image = dict(id=123456, slug=p.IMAGE, public=True, status="available",
                          distribution="Ubuntu", regions=["lon1"])
        self.paths = []

    def find(self, collection, slug):
        return self.size if collection == "sizes" else self.region

    def get(self, path):
        self.paths.append(path)
        return {"image": self.image}


class ProviderPreflightTests(unittest.TestCase):
    def test_happy_path_is_bounded_public_output(self):
        c = FakeCatalogue()
        c.size["private_extra"] = "DO_NOT_PRINT"
        report = p.evaluate(c)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["resources_created"], 0)
        self.assertFalse(report["cleanup_armed"])
        self.assertFalse(report["deletion_permission_verified"])
        self.assertFalse(report["account_creation_eligibility_verified"])
        self.assertNotIn("DO_NOT_PRINT", json.dumps(report))
        self.assertEqual(c.paths, ["/v2/images/ubuntu-22-04-x64"])

    def test_wrong_dimensions_fail(self):
        for key in ("vcpus", "memory", "disk"):
            with self.subTest(key=key):
                c = FakeCatalogue()
                c.size[key] += 1
                with self.assertRaises(p.CheckError):
                    p.evaluate(c)

    def test_non_integer_dimensions_fail(self):
        c = FakeCatalogue()
        c.size["vcpus"] = 4.0
        with self.assertRaises(p.CheckError):
            p.evaluate(c)

    def test_price_guard(self):
        for price in ("0.073", "NaN", "Infinity", "-1", "0", None, "garbage"):
            with self.subTest(price=price):
                c = FakeCatalogue()
                c.size["price_hourly"] = price
                with self.assertRaises(p.CheckError):
                    p.evaluate(c)

    def test_unavailable_region_or_plan_fails(self):
        for attr in ("size", "region"):
            c = FakeCatalogue()
            getattr(c, attr)["available"] = False
            with self.assertRaises(p.CheckError):
                p.evaluate(c)
        c = FakeCatalogue()
        c.size["regions"] = ["nyc1"]
        with self.assertRaises(p.CheckError):
            p.evaluate(c)

    def test_only_public_matching_image(self):
        for key, value in (("public", False), ("id", True), ("slug", "ubuntu-24-04-x64"),
                           ("distribution", "Debian"), ("status", "deleted"), ("regions", [])):
            with self.subTest(key=key):
                c = FakeCatalogue()
                c.image[key] = value
                with self.assertRaises(p.CheckError):
                    p.evaluate(c)

    def test_only_catalogue_paths_are_allowed(self):
        for path in ("/v2/droplets", "/v2/droplets/1", "/v2/account", "https://example.com",
                     "/v2/regions?per_page=200&page=11", "/v2/images/123",
                     "/v2/sizes?per_page=200&page=1&extra=yes"):
            with self.subTest(path=path):
                self.assertFalse(p.allowed_path(path))
        self.assertTrue(p.allowed_path("/v2/sizes?per_page=200&page=10"))

    def test_missing_token_never_contacts_provider(self):
        with patch.object(p, "build_opener") as opener:
            with self.assertRaises(p.CheckError):
                p.Catalogue("")
            opener.assert_not_called()

    def test_bad_path_never_contacts_provider(self):
        client = p.Catalogue("synthetic-token")
        with patch.object(client._opener, "open") as request:
            with self.assertRaises(p.CheckError):
                client.get("/v2/droplets/1")
            request.assert_not_called()

    def test_http_failure_does_not_echo_response(self):
        client = p.Catalogue("synthetic-token")
        error = HTTPError("https://api.digitalocean.com", 403, "PRIVATE_ERROR", {}, io.BytesIO(b"PRIVATE_BODY"))
        with patch.object(client._opener, "open", side_effect=error):
            with self.assertRaisesRegex(p.CheckError, "^provider_auth_or_read_scope_rejected$"):
                client.get("/v2/images/ubuntu-22-04-x64")

    def test_transport_only_uses_get_and_fixed_origin(self):
        client = p.Catalogue("synthetic-token")
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"sizes": []}'
        with patch.object(client._opener, "open", return_value=response) as op:
            client.get("/v2/sizes?per_page=200&page=1")
            req = op.call_args.args[0]
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.full_url, "https://api.digitalocean.com/v2/sizes?per_page=200&page=1")

    def test_pagination_never_follows_untrusted_links(self):
        client = p.Catalogue("synthetic-token")
        pages = [{"sizes": [], "links": {"pages": {"next": "https://evil.invalid/steal"}}},
                 {"sizes": [{"slug": p.SIZE}]}]
        with patch.object(client, "get", side_effect=pages) as get:
            client.find("sizes", p.SIZE)
            self.assertEqual(get.call_args_list[1].args[0], "/v2/sizes?per_page=200&page=2")

    def test_no_match_is_not_success(self):
        client = p.Catalogue("synthetic-token")
        with patch.object(client, "get", return_value={"sizes": []}):
            with self.assertRaises(p.CheckError):
                client.find("sizes", p.SIZE)

    def test_failure_summary_contains_no_secret(self):
        buffer = io.StringIO()
        with patch.dict("os.environ", {"SQUADPAD_DO_TOKEN": "", "GITHUB_STEP_SUMMARY": ""}):
            with redirect_stdout(buffer):
                self.assertEqual(p.main(), 1)
        self.assertEqual(json.loads(buffer.getvalue())["reason"], "cleanup_token_missing_or_invalid")


if __name__ == "__main__":
    unittest.main()
