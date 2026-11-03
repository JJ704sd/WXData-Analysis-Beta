"""Synthetic, data-free checks for the private-analysis Skill distributor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from scripts import package_private_chat
from chat_release import PRIVATE_VERSION


class PrivateChatDistributionTests(unittest.TestCase):
    @staticmethod
    def _capability_probe(command, **kwargs):
        role = "profile" if "profile.exe" in command[0] else "desktop"
        value = {"schemaVersion": 1, "privateAnalysis": True,
                 "conversationMode": "direct", "role": role, "version": PRIVATE_VERSION}
        if role == "desktop":
            result_path = Path(command[command.index("--result-json") + 1])
            assert not result_path.exists()
            result_path.write_text(json.dumps(value), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    def test_builds_two_bound_allowlisted_packages_from_explicit_exes(self):
        with tempfile.TemporaryDirectory(prefix="private-chat-package-") as temporary:
            root = Path(temporary)
            desktop = root / "new-private-desktop.exe"
            profile = root / "new-private-profile.exe"
            desktop.write_bytes(b"synthetic-private-desktop")
            profile.write_bytes(b"synthetic-private-profile")
            output = root / "out"
            with patch.object(package_private_chat.subprocess, "run", side_effect=self._capability_probe) as probe:
                self.assertEqual(package_private_chat.main([
                    "--desktop-exe", str(desktop),
                    "--profile-cli-exe", str(profile),
                    "--output-root", str(output),
                    "--version", PRIVATE_VERSION,
                ]), 0)
            self.assertEqual(probe.call_count, 2)
            self.assertTrue(all("--capabilities" in call.args[0] for call in probe.call_args_list))
            desktop_call = next(call for call in probe.call_args_list if "desktop.exe" in call.args[0][0])
            self.assertIn("--result-json", desktop_call.args[0])
            self.assertIn("--conversation-mode", desktop_call.args[0])
            self.assertEqual(desktop_call.args[0][desktop_call.args[0].index("--conversation-mode") + 1], "direct")
            packages = sorted(output.glob("*.zip"))
            self.assertEqual([path.name for path in packages], [
                f"local-wechat-private-analysis-{PRIVATE_VERSION}-windows-x64.zip",
                f"local-wecom-private-analysis-{PRIVATE_VERSION}-windows-x64.zip",
            ])
            for archive in packages:
                with zipfile.ZipFile(archive) as bundle:
                    self.assertIsNone(bundle.testzip())
                    names = set(bundle.namelist())
                    self.assertTrue({
                        "SKILL.md", "Start.cmd", "scripts/run.ps1", "agents/openai.yaml",
                        "bin/local-chat-desktop.exe", "bin/private-chat-profile.exe",
                        "runtime.json", "README.md", "package.json", "SHA256SUMS.txt",
                    }.issubset(names))
                    self.assertTrue(any(name.startswith("licenses/") for name in names))
                    runtime = json.loads(bundle.read("runtime.json"))
                    metadata = json.loads(bundle.read("package.json"))
                    readme = bundle.read("README.md").decode("utf-8")
                    self.assertEqual(runtime["conversationMode"], "direct")
                    self.assertTrue(runtime["privateAnalysis"])
                    self.assertEqual(runtime["version"], PRIVATE_VERSION)
                    self.assertEqual(runtime["exeRelativePath"], "bin/local-chat-desktop.exe")
                    self.assertFalse(metadata["containsChatData"])
                    self.assertFalse(metadata["containsKeys"])
                    self.assertEqual(metadata["desktopExeSha256"], package_private_chat.sha256(desktop))
                    self.assertEqual(metadata["profileCliSha256"], package_private_chat.sha256(profile))
                    self.assertIn("聊天时光", readme)
                    self.assertIn("不含消息原文", readme)
                    self.assertIn("不承诺随机或日历接口", readme)
                    self.assertIn("三阶段", readme)
                    self.assertIn("清单失败时保留正文", readme)
                    self.assertIn("按会话进行", readme)
                    checksums = bundle.read("SHA256SUMS.txt").decode("utf-8")
                    self.assertIn("bin/local-chat-desktop.exe", checksums)
                    self.assertIn("bin/private-chat-profile.exe", checksums)

    def test_refuses_old_1_0_exe_from_pretending_to_be_1_1(self):
        with tempfile.TemporaryDirectory(prefix="private-chat-package-") as temporary:
            root = Path(temporary) / "ordinary-build"
            root.mkdir()
            desktop = root / "desktop.exe"
            profile = Path(temporary) / "profile.exe"
            desktop.write_bytes(b"desktop")
            profile.write_bytes(b"profile")
            def old_probe(command, **kwargs):
                value = {
                    "schemaVersion": 1, "privateAnalysis": True,
                    "conversationMode": "direct", "role": "desktop", "version": "1.0.0",
                }
                result_path = Path(command[command.index("--result-json") + 1])
                result_path.write_text(json.dumps(value), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")
            with patch.object(package_private_chat.subprocess, "run", side_effect=old_probe):
                with self.assertRaises(SystemExit):
                    package_private_chat.main([
                        "--desktop-exe", str(desktop),
                        "--profile-cli-exe", str(profile),
                        "--output-root", str(Path(temporary) / "out"),
                    ])

    def test_license_allowlist_rejects_chat_settings_and_reparse_trees(self):
        with tempfile.TemporaryDirectory(prefix="private-chat-license-") as temporary:
            root = Path(temporary)
            mixed = root / "mixed"
            mixed.mkdir()
            (mixed / "LICENSE.txt").write_text("synthetic license", encoding="utf-8")
            (mixed / "settings.local.json").write_text("{}", encoding="utf-8")
            (mixed / "chat.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                package_private_chat.license_files(root, mixed)

            linked = root / "linked"
            linked.mkdir()
            target = root / "target"
            target.mkdir()
            (target / "LICENSE.txt").write_text("synthetic license", encoding="utf-8")
            try:
                os.symlink(str(target), str(linked / "license-tree"), target_is_directory=True)
            except (OSError, NotImplementedError):
                # Keep the reverse test meaningful on hosts without symlink
                # privileges by simulating the reparse-point probe.
                (linked / "license-tree").mkdir()
                (linked / "license-tree" / "LICENSE.txt").write_text("synthetic license", encoding="utf-8")
                with patch.object(package_private_chat, "_is_reparse",
                                   side_effect=lambda path: path.name == "license-tree"):
                    with self.assertRaises(SystemExit):
                        package_private_chat.license_files(root, linked)
            else:
                with self.assertRaises(SystemExit):
                    package_private_chat.license_files(root, linked)

    def test_skill_wrappers_are_client_bound_and_direct_only(self):
        project = Path(__file__).resolve().parents[1]
        for client in ("wechat", "wecom"):
            skill = project / "skills" / f"local-{client}-private-analysis"
            wrapper = (skill / "scripts/run.ps1").read_text(encoding="utf-8")
            document = (skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"$BoundClient = '{client}'", wrapper)
            self.assertIn("conversationMode='direct'", wrapper)
            self.assertIn("--conversation-mode' 'direct'", wrapper)
            self.assertIn("'--bound-client' $BoundClient", wrapper)
            self.assertIn("ProfileInsights", wrapper)
            self.assertIn("$OutputDirectory", wrapper)
            self.assertIn("$Keyword", wrapper)
            self.assertIn("$Offset", wrapper)
            self.assertIn("$Limit", wrapper)
            for flag in ("insights", "--export-root", "--output-dir", "--keyword", "--offset", "--limit"):
                self.assertIn(flag, wrapper)
            self.assertIn("Try-ReadJson", wrapper)
            self.assertIn("Wait-ReadableJson", wrapper)
            self.assertIn("state=waiting", wrapper)
            self.assertIn("state=failed", wrapper)
            self.assertIn("Test-SameOrUnder", wrapper)
            self.assertIn("OutputDirectory must be a new directory", wrapper)
            self.assertIn("outside the formal export and profile store", wrapper)
            self.assertIn("ProfileStage", wrapper)
            self.assertIn("ProfileReview", wrapper)
            self.assertIn("coveredChunks", document)
            self.assertIn("人工复核", document)
            self.assertIn("聊天时光", document)
            self.assertIn("不含消息原文", document)


if __name__ == "__main__":
    unittest.main()
