from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.check_no_secret_literals import main
from scripts.scan_artifacts_for_secrets import main as scan_main


class SecretScanScriptTests(unittest.TestCase):
    def test_clean_dir_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("clean", encoding="utf-8")

            self.assertEqual(main(["--root", tmp]), 0)

    def test_secret_literal_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("token ghp_must_not_leak", encoding="utf-8")

            self.assertEqual(main(["--root", tmp]), 1)

    def test_output_prints_pattern_type_not_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("token ghp_must_not_leak", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(["--root", tmp])

            self.assertEqual(code, 1)
            self.assertIn("pattern=github-classic-token", stdout.getvalue())
            self.assertNotIn("ghp_must_not_leak", stdout.getvalue())

    def test_scan_artifacts_wrapper_uses_same_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("clean", encoding="utf-8")

            self.assertEqual(scan_main(["--root", tmp]), 0)

    def test_openai_token_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("key: sk-fakeTestKey1234567890", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_aws_key_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("AKIA0123456789ABCDEF", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_private_key_header_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_bearer_token_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.fake.fake", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_slack_bot_token_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("token=xoxb-fakeTestToken12345", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_no_false_positive_on_short_aws_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("AKIA", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 0)

    def test_no_false_positive_on_short_openai_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("sk-proj", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 0)

    def test_github_fine_grained_token_fake_sample_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.md").write_text("token=github_pat_fakeTestToken12345", encoding="utf-8")
            self.assertEqual(main(["--root", tmp]), 1)

    def test_multiple_patterns_in_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.html").write_text(
                "ghp_fake12345 sk-fakeTestKey12345 AKIA0123456789ABCDEF", encoding="utf-8"
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["--root", tmp])
            self.assertEqual(code, 1)
            output = stdout.getvalue()
            self.assertIn("github-classic-token", output)
            self.assertIn("openai-token", output)
            self.assertIn("aws-access-key", output)


if __name__ == "__main__":
    unittest.main()
