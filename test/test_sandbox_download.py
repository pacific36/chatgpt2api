from __future__ import annotations

import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from services.protocol import sandbox_files


class FakeBackend:
    def __init__(self, access_token: str = "tok"):
        self.access_token = access_token


class ResolveSandboxLinksTests(unittest.TestCase):
    """resolve_sandbox_links now builds signed lazy proxy URLs (no network)."""

    def setUp(self) -> None:
        self.ref_patch = mock.patch(
            "services.account_service.account_service.account_ref_for_token",
            return_value="user-123",
        )
        from services.config import config

        self.auth_patch = mock.patch.object(
            type(config), "auth_key", new_callable=mock.PropertyMock, return_value="secret-key"
        )
        self.ref_patch.start()
        self.auth_patch.start()
        self.addCleanup(self.ref_patch.stop)
        self.addCleanup(self.auth_patch.stop)

    def _parse(self, url: str) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}

    def test_markdown_link_becomes_signed_proxy_url(self):
        backend = FakeBackend()
        text = "done\n\n[Download CSV](sandbox:/mnt/data/r.csv)"
        result = sandbox_files.resolve_sandbox_links(text, backend, "cid-1", "mid-1", "http://host")
        self.assertNotIn("sandbox:", result)
        self.assertIn("[Download CSV](http://host/sandbox-files?", result)
        url = result.split("](", 1)[1].rstrip(")")
        params = self._parse(url)
        self.assertEqual(params["cid"], "cid-1")
        self.assertEqual(params["mid"], "mid-1")
        self.assertEqual(params["p"], "/mnt/data/r.csv")
        self.assertEqual(params["a"], "user-123")
        # signature verifies against the same secret
        self.assertTrue(
            sandbox_files.verify_sandbox_signature(
                "secret-key", "cid-1", "mid-1", "/mnt/data/r.csv", "user-123", params["s"]
            )
        )

    def test_bare_link_becomes_proxy_url(self):
        backend = FakeBackend()
        result = sandbox_files.resolve_sandbox_links("see sandbox:/mnt/data/a.txt here", backend, "cid", "mid", "http://host")
        self.assertIn("[a.txt](http://host/sandbox-files?", result)

    def test_missing_conversation_id_degrades_to_note(self):
        result = sandbox_files.resolve_sandbox_links("[X](sandbox:/mnt/data/x.csv)", FakeBackend(), "", "mid", "http://host")
        self.assertIn(sandbox_files.SANDBOX_NOTE, result)
        self.assertNotIn("/sandbox-files?", result)

    def test_missing_account_ref_degrades_to_note(self):
        with mock.patch("services.account_service.account_service.account_ref_for_token", return_value=""):
            result = sandbox_files.resolve_sandbox_links("[X](sandbox:/mnt/data/x.csv)", FakeBackend(), "cid", "mid", "http://host")
        self.assertIn(sandbox_files.SANDBOX_NOTE, result)

    def test_tampered_signature_rejected(self):
        self.assertFalse(
            sandbox_files.verify_sandbox_signature("secret-key", "cid", "mid", "/mnt/data/x", "ref", "deadbeef")
        )

    def test_signature_bound_to_every_field(self):
        good = sandbox_files._sign("secret-key", "cid", "mid", "/p", "ref")
        self.assertFalse(sandbox_files.verify_sandbox_signature("secret-key", "cid", "mid", "/p2", "ref", good))
        self.assertFalse(sandbox_files.verify_sandbox_signature("other-key", "cid", "mid", "/p", "ref", good))


class FetchSandboxFileTests(unittest.TestCase):
    def setUp(self) -> None:
        from services.config import config

        self.auth_patch = mock.patch.object(
            type(config), "auth_key", new_callable=mock.PropertyMock, return_value="secret-key"
        )
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def _sig(self, *parts: str) -> str:
        return sandbox_files._sign("secret-key", *parts)

    def test_bad_signature_raises_link_error(self):
        with self.assertRaises(sandbox_files.SandboxLinkError):
            sandbox_files.fetch_sandbox_file("cid", "mid", "/p", "ref", "wrong")

    def test_missing_account_raises(self):
        sig = self._sig("cid", "mid", "/p", "ref")
        with mock.patch("services.account_service.account_service.get_token_by_account_ref", return_value=""):
            with self.assertRaises(RuntimeError):
                sandbox_files.fetch_sandbox_file("cid", "mid", "/p", "ref", sig)

    def test_happy_path_downloads_through_owning_account(self):
        sig = self._sig("cid", "mid", "/mnt/data/x.csv", "ref")
        fake_backend = mock.Mock()
        fake_backend.download_sandbox_file.return_value = (b"x,y\n1,2\n", "x.csv", "text/csv")
        with mock.patch("services.account_service.account_service.get_token_by_account_ref", return_value="tok"), \
             mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=fake_backend):
            data, name, mime = sandbox_files.fetch_sandbox_file("cid", "mid", "/mnt/data/x.csv", "ref", sig)
        self.assertEqual(data, b"x,y\n1,2\n")
        self.assertEqual(name, "x.csv")
        fake_backend.download_sandbox_file.assert_called_once_with("cid", "mid", "/mnt/data/x.csv")

    def test_missing_mid_derived_from_detail(self):
        sig = self._sig("cid", "", "/mnt/data/x.csv", "ref")
        fake_backend = mock.Mock()
        fake_backend._get_conversation.return_value = {"mapping": {"n": {"message": {"id": "derived-mid"}}}}
        fake_backend.download_sandbox_file.return_value = (b"d", "x.csv", "text/csv")
        with mock.patch("services.account_service.account_service.get_token_by_account_ref", return_value="tok"), \
             mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=fake_backend):
            sandbox_files.fetch_sandbox_file("cid", "", "/mnt/data/x.csv", "ref", sig)
        fake_backend.download_sandbox_file.assert_called_once_with("cid", "derived-mid", "/mnt/data/x.csv")


class ResolverPollingTests(unittest.TestCase):
    def _backend(self):
        from services.openai_backend_api import OpenAIBackendAPI

        return OpenAIBackendAPI(access_token="tok")

    def test_polls_until_download_url_ready(self):
        backend = self._backend()
        responses = [
            mock.Mock(status_code=200, json=mock.Mock(return_value={"status": "retry"})),
            mock.Mock(status_code=200, json=mock.Mock(return_value={"status": "retry"})),
            mock.Mock(status_code=200, json=mock.Mock(return_value={"status": "success", "download_url": "https://x/y"})),
        ]
        with mock.patch.object(backend.session, "get", side_effect=responses) as getter, \
             mock.patch("services.openai_backend_api.time.sleep"):
            data = backend.resolve_sandbox_download("cid", "mid", "/mnt/data/x.csv", max_wait_secs=10)
        self.assertEqual(data["download_url"], "https://x/y")
        self.assertEqual(getter.call_count, 3)

    def test_gives_up_after_deadline(self):
        backend = self._backend()
        retry = mock.Mock(status_code=200, json=mock.Mock(return_value={"status": "retry"}))
        first = iter([0.0])
        with mock.patch.object(backend.session, "get", return_value=retry) as getter, \
             mock.patch("services.openai_backend_api.time.sleep"), \
             mock.patch("services.openai_backend_api.time.time", side_effect=lambda: next(first, 100.0)):
            data = backend.resolve_sandbox_download("cid", "mid", "/mnt/data/x.csv", max_wait_secs=10)
        self.assertNotIn("download_url", data)
        self.assertEqual(getter.call_count, 1)


if __name__ == "__main__":
    unittest.main()
