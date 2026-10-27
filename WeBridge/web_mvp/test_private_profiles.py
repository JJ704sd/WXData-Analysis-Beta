"""Synthetic coverage for the private profile store; no real chats or accounts."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import private_profiles
from private_profiles import ProfileError, ProfileStore


class PrivateProfileFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="private-profile-")
        self.base = Path(self.tmp.name)
        self.export = self.base / "export"
        (self.export / "analysis").mkdir(parents=True)
        (self.export / "analysis" / "part-001.md").write_text("合成私聊材料", encoding="utf-8")
        (self.export / "messages.jsonl").write_text(
            json.dumps({"id": "m1", "ref": 1, "time": "2026-10-22T09:00:00+08:00",
                        "senderId": "peer-1", "direction": "incoming", "text": "需要报价"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (self.export / "analysis" / "corpus.jsonl").write_text(
            json.dumps({"id": "m1", "ref": 1, "senderId": "peer-1", "text": "需要报价"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (self.export / "analysis" / "index.json").write_text(
            json.dumps({"chunks": [{"file": "part-001.md", "refs": [1, 1]}]}), encoding="utf-8")
        reviewed = []
        for path in sorted(self.export.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                reviewed.append({"path": path.relative_to(self.export).as_posix(), "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"schemaVersion": 1, "client": "wechat", "selfId": "self-1",
                    "chat": {"id": "peer-1", "name": "合成联系人", "conversationKind": "direct"},
                    "exportedRows": 1, "startDate": None, "endDate": None,
                    "humanReview": {"status": "confirmed", "reviewId": "review-1"}}
        review = {"status": "confirmed", "reviewId": "review-1", "peerId": "peer-1", "selfId": "self-1",
                  "client": "wechat", "startDate": None, "endDate": None, "chat": manifest["chat"],
                  "confirmationBasis": "explicit_review_checklist",
                  "checks": {key: True for key in ("accountGroup", "rangeCount", "content", "gaps")},
                  "reviewedArtifacts": reviewed}
        (self.export / "review.json").write_text(json.dumps(review), encoding="utf-8")
        artifact_files = []
        for path in sorted(self.export.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                artifact_files.append({"path": path.relative_to(self.export).as_posix(), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})
        manifest["artifacts"] = artifact_files + [{"path": "review.json", "bytes": (self.export / "review.json").stat().st_size,
                                                     "sha256": hashlib.sha256((self.export / "review.json").read_bytes()).hexdigest()}]
        (self.export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.store = ProfileStore(self.base / "profiles")

    def tearDown(self):
        self.tmp.cleanup()

    def candidate(self, request, *, text="对方明确提出报价需求", covered=None):
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "baseRevision": request["baseRevision"],
                "coveredChunks": request["requiredChunks"] if covered is None else covered,
                "observations": [{"subject": "peer", "category": "needs", "basis": "explicit",
                    "text": text, "evidence": [{"sourceId": request["sources"][0]["sourceId"], "ref": 1}]}],
                "limitations": ["合成测试材料有限"]}

    def test_prepare_stage_confirm_current_and_idempotency(self):
        request = self.store.prepare(self.export)
        self.assertEqual(request["status"], "ready")
        self.assertTrue(request["materialFiles"] and Path(request["materialFiles"][0]).is_file())
        self.assertIsNone(request["currentProfileFile"])
        staged = self.store.stage(request["requestId"], self.candidate(request))
        loaded = self.store.candidate(staged["candidateId"])
        self.assertEqual(loaded["candidateId"], staged["candidateId"])
        self.assertEqual(loaded["changes"][0]["text"], "对方明确提出报价需求")
        account_key = request["accountKey"]
        self.assertIsNone(self.store.current("wechat", account_key, "peer-1"))
        confirmed = self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer", note="合成测试")
        self.assertEqual(confirmed["revision"], self.store.current("wechat", account_key, "peer-1")["revision"])
        repeated = self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        self.assertEqual(repeated["revision"], confirmed["revision"])
        self.assertTrue(Path(confirmed["profileFile"]).is_file())
        self.assertTrue(Path(confirmed["markdownFile"]).is_file())
        updated = self.store.prepare(self.export)
        self.assertEqual(updated["currentProfileFile"], confirmed["profileFile"])

    def test_rejects_missing_coverage_and_fake_evidence(self):
        request = self.store.prepare(self.export)
        with self.assertRaises(ProfileError):
            self.store.stage(request["requestId"], self.candidate(request, covered=[]))
        fake = self.candidate(request)
        fake["observations"][0]["evidence"][0]["ref"] = 99
        with self.assertRaises(ProfileError):
            self.store.stage(request["requestId"], fake)

    def test_stale_base_is_rejected_and_overview_is_self_relationship_only(self):
        request = self.store.prepare(self.export)
        staged = self.store.stage(request["requestId"], self.candidate(request))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        self.assertEqual(self.store.overview("wechat", request["accountKey"])["observations"], [])
        with self.assertRaises(ProfileError):
            self.store.stage(request["requestId"], self.candidate(request, text="同一观察"))

    def test_group_or_unreviewed_export_is_rejected(self):
        manifest = json.loads((self.export / "manifest.json").read_text())
        manifest["chat"]["conversationKind"] = "group"
        (self.export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.prepare(self.export)

    def test_unconfirmed_prepare_is_stable_then_confirmed_source_is_overlap(self):
        first = self.store.prepare(self.export)
        second = self.store.prepare(self.export)
        self.assertEqual(first["requestId"], second["requestId"])
        staged = self.store.stage(first["requestId"], self.candidate(first))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        third = self.store.prepare(self.export)
        self.assertEqual(third["status"], "unchanged")
        self.assertEqual(third["delta"]["new"], [])
        self.assertEqual(third["delta"]["changed"], [])

    def test_unchanged_prepare_cannot_publish_duplicate_revision(self):
        first = self.store.prepare(self.export)
        staged = self.store.stage(first["requestId"], self.candidate(first))
        confirmed = self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        unchanged = self.store.prepare(self.export)
        self.assertEqual(unchanged["status"], "unchanged")
        empty = {"schemaVersion": 1, "requestId": unchanged["requestId"],
                 "baseRevision": unchanged["baseRevision"],
                 "coveredChunks": unchanged["requiredChunks"], "observations": [],
                 "limitations": []}
        with self.assertRaises(ProfileError):
            self.store.stage(unchanged["requestId"], empty)
        self.assertEqual(self.store.current("wechat", first["accountKey"], "peer-1")["revision"],
                         confirmed["revision"])

    def test_overview_write_failure_does_not_leave_partial_json(self):
        request = self.store.prepare(self.export)
        candidate = self.candidate(request)
        candidate["observations"][0]["subject"] = "self"
        candidate["observations"][0]["basis"] = "observed"
        staged = self.store.stage(request["requestId"], candidate)
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        baseline = self.store.overview("wechat", request["accountKey"])
        json_path, markdown_path = Path(baseline["jsonFile"]), Path(baseline["markdownFile"])
        old_json, old_markdown = json_path.read_bytes(), markdown_path.read_bytes()

        with patch.object(private_profiles, "_write_text",
                          side_effect=OSError("synthetic markdown failure")):
            with self.assertRaises(ProfileError):
                self.store.overview("wechat", request["accountKey"])
        self.assertEqual(json_path.read_bytes(), old_json)
        self.assertEqual(markdown_path.read_bytes(), old_markdown)

    def test_overview_ignores_corrupt_profile_for_another_account(self):
        first = self.store.prepare(self.export)
        staged = self.store.stage(first["requestId"], self.candidate(first))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")

        other_export = self.base / "export-other-account"
        shutil.copytree(self.export, other_export)
        manifest_path = other_export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["selfId"] = "self-2"
        manifest["humanReview"]["reviewId"] = "review-2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        review_path = other_export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["selfId"] = "self-2"
        review["reviewId"] = "review-2"
        review_path.write_text(json.dumps(review), encoding="utf-8")

        def digest(path):
            return {"path": path.relative_to(other_export).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewedArtifacts"] = [digest(other_export / item["path"])
                                       for item in review["reviewedArtifacts"]]
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = [digest(path) for path in sorted(other_export.rglob("*"))
                                 if path.is_file() and path.name != "manifest.json"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        other = self.store.prepare(other_export)
        staged_other = self.store.stage(other["requestId"], self.candidate(other))
        self.store.confirm(staged_other["candidateId"], confirmed_by="synthetic reviewer")
        other_identity = {"client": "wechat", "accountKey": other["accountKey"], "peerId": "peer-1"}
        other_dir = self.store.root / "profiles" / self.store._identity_key(other_identity)
        pointer = json.loads((other_dir / "current.json").read_text(encoding="utf-8"))
        (other_dir / pointer["profileFile"]).write_text("{}", encoding="utf-8")

        overview = self.store.overview("wechat", first["accountKey"])
        self.assertEqual(overview["contacts"], ["peer-1"])
        first_identity = {"client": "wechat", "accountKey": first["accountKey"], "peerId": "peer-1"}
        first_dir = self.store.root / "profiles" / self.store._identity_key(first_identity)
        first_pointer_path = first_dir / "current.json"
        first_pointer_raw = first_pointer_path.read_bytes()
        for raw in (b"{bad", b"[]"):
            first_pointer_path.write_bytes(raw)
            with self.assertRaises(ProfileError):
                self.store.overview("wechat", first["accountKey"])
        first_pointer_path.write_bytes(first_pointer_raw)
        first_pointer = json.loads(first_pointer_raw.decode("utf-8"))
        (first_dir / first_pointer["profileFile"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.overview("wechat", first["accountKey"])

    def test_candidate_readback_has_resolved_evidence_and_hash_tamper_fails(self):
        request = self.store.prepare(self.export)
        staged = self.store.stage(request["requestId"], self.candidate(request))
        loaded = self.store.candidate(staged["candidateId"])
        evidence = loaded["observations"][0]["evidence"][0]
        self.assertEqual(evidence["text"], "需要报价")
        self.assertEqual(evidence["senderId"], "peer-1")
        self.assertEqual(evidence["file"], "messages.jsonl")
        path = Path(staged["candidateFile"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["observations"][0]["text"] = "篡改"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.candidate(staged["candidateId"])

    def test_review_checks_and_path_ids_fail_closed(self):
        review_path = self.export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["checks"]["content"] = False
        review_path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.prepare(self.export)
        with self.assertRaises(ProfileError):
            self.store.candidate("../../secret")

    def test_request_tamper_is_detected(self):
        request = self.store.prepare(self.export)
        path = Path(request["requestFile"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["requiredChunks"] = []
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.request(request["requestId"])

    def test_request_rejects_extra_source_and_metadata_changes(self):
        request = self.store.prepare(self.export)
        path = Path(request["requestFile"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sources"].append(dict(value["sources"][0]))
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.request(request["requestId"])
        path.unlink()

        request = self.store.prepare(self.export)
        path = Path(request["requestFile"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sources"][0]["timeRange"] = {"start": "tampered", "end": "tampered"}
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.request(request["requestId"])

    def test_preexisting_revision_and_pointer_digest_fail_closed(self):
        request = self.store.prepare(self.export)
        staged = self.store.stage(request["requestId"], self.candidate(request))
        revision = "rev-" + hashlib.sha256(json.dumps(
            {"candidateId": staged["candidateId"], "base": request["baseRevision"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
        identity_key = hashlib.sha256(json.dumps(
            {"client": request["client"], "accountKey": request["accountKey"], "peerId": request["peerId"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        revision_dir = self.store.root / "profiles" / identity_key / "revisions" / revision
        revision_dir.mkdir(parents=True)
        (revision_dir / "profile.json").write_text(json.dumps({"candidateId": staged["candidateId"]}), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")

        # A normally published version carries and verifies all three file digests.
        shutil_target = revision_dir
        for child in list(shutil_target.iterdir()):
            child.unlink()
        shutil_target.rmdir()
        confirmed = self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        pointer = self.store.root / "profiles" / identity_key / "current.json"
        pointer_value = json.loads(pointer.read_text(encoding="utf-8"))
        self.assertEqual(set(pointer_value["sha256"]), {"profileFile", "markdownFile", "confirmationFile"})
        Path(confirmed["markdownFile"]).write_text("篡改", encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.current(request["client"], request["accountKey"], request["peerId"])

    def test_source_cache_tamper_is_not_reported_as_real_evidence(self):
        request = self.store.prepare(self.export)
        staged = self.store.stage(request['requestId'], self.candidate(request))
        self.store.confirm(staged['candidateId'], confirmed_by='synthetic reviewer')
        path = self.store.root / 'sources' / (request['sources'][0]['sourceId'] + '.json')
        value = json.loads(path.read_text(encoding='utf8'))
        value['records'][0]['text'] = 'fabricated source text'
        path.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaises(ProfileError):
            self.store.request(request['requestId'])
        with self.assertRaises(ProfileError):
            self.store.prepare(self.export)

    def test_same_material_new_review_batch_keeps_old_source_and_is_unchanged(self):
        first = self.store.prepare(self.export)
        staged = self.store.stage(first["requestId"], self.candidate(first))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        old_request_id = first["requestId"]
        old_source_id = first["sources"][0]["sourceId"]

        second_export = self.base / "export-review-2"
        shutil.copytree(self.export, second_export)
        review_path = second_export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewId"] = "review-2"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest_path = second_export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["humanReview"]["reviewId"] = "review-2"
        review_digest = hashlib.sha256(review_path.read_bytes()).hexdigest()
        for item in manifest["artifacts"]:
            if item.get("path") == "review.json":
                item["sha256"] = review_digest
                item["bytes"] = review_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        second = self.store.prepare(second_export)
        self.assertNotEqual(second["sources"][0]["sourceId"], old_source_id)
        self.assertEqual(second["status"], "unchanged")
        self.store.request(old_request_id)
        self.assertTrue((self.store.root / "sources" / (old_source_id + ".json")).is_file())
        current = self.store.current("wechat", first["accountKey"], "peer-1")
        self.assertEqual(current["sourceIds"], [old_source_id])

    def test_reusing_an_older_source_keeps_it_latest_for_incremental_prepare(self):
        first = self.store.prepare(self.export)
        staged = self.store.stage(first["requestId"], self.candidate(first, text="观察A"))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")

        second_export = self.base / "export-review-2-changed"
        shutil.copytree(self.export, second_export)
        messages_path = second_export / "messages.jsonl"
        message = json.loads(messages_path.read_text(encoding="utf-8").splitlines()[0])
        message["text"] = "需要报价B"
        messages_path.write_text(json.dumps(message, ensure_ascii=False) + "\n", encoding="utf-8")
        corpus_path = second_export / "analysis" / "corpus.jsonl"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8").splitlines()[0])
        corpus["text"] = "需要报价B"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False) + "\n", encoding="utf-8")

        review_path = second_export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewId"] = "review-2-changed"
        for item in review["reviewedArtifacts"]:
            target = second_export / item["path"]
            item.update(bytes=target.stat().st_size,
                        sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest_path = second_export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["humanReview"]["reviewId"] = "review-2-changed"
        for item in manifest["artifacts"]:
            target = second_export / item["path"]
            if target.is_file():
                item.update(bytes=target.stat().st_size,
                            sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        second = self.store.prepare(second_export)
        self.assertEqual(second["delta"]["changed"], ["id:m1"])
        staged_second = self.store.stage(second["requestId"], self.candidate(second, text="观察B"))
        self.store.confirm(staged_second["candidateId"], confirmed_by="synthetic reviewer")

        rollback = self.store.prepare(self.export)
        self.assertEqual(rollback["status"], "ready")
        staged_rollback = self.store.stage(rollback["requestId"], self.candidate(rollback, text="观察A"))
        self.store.confirm(staged_rollback["candidateId"], confirmed_by="synthetic reviewer")

        repeated = self.store.prepare(self.export)
        self.assertEqual(repeated["status"], "unchanged")
        current = self.store.current("wechat", first["accountKey"], "peer-1")
        self.assertEqual(current["sourceIds"], [second["sources"][0]["sourceId"],
                                                 first["sources"][0]["sourceId"]])

        third_export = self.base / "export-review-3-new-source"
        shutil.copytree(second_export, third_export)
        third_review_path = third_export / "review.json"
        third_review = json.loads(third_review_path.read_text(encoding="utf-8"))
        third_review["reviewId"] = "review-3-new-source"
        for item in third_review["reviewedArtifacts"]:
            target = third_export / item["path"]
            item.update(bytes=target.stat().st_size,
                        sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        third_review_path.write_text(json.dumps(third_review), encoding="utf-8")
        third_manifest_path = third_export / "manifest.json"
        third_manifest = json.loads(third_manifest_path.read_text(encoding="utf-8"))
        third_manifest["humanReview"]["reviewId"] = "review-3-new-source"
        for item in third_manifest["artifacts"]:
            target = third_export / item["path"]
            if target.is_file():
                item.update(bytes=target.stat().st_size,
                            sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        third_manifest_path.write_text(json.dumps(third_manifest), encoding="utf-8")
        third = self.store.prepare(third_export)
        self.assertEqual(third["status"], "ready")
        staged_third = self.store.stage(third["requestId"], self.candidate(third, text="观察C"))
        self.store.confirm(staged_third["candidateId"], confirmed_by="synthetic reviewer")
        current = self.store.current("wechat", first["accountKey"], "peer-1")
        self.assertEqual(current["sourceIds"], [second["sources"][0]["sourceId"],
                                                 first["sources"][0]["sourceId"],
                                                 third["sources"][0]["sourceId"]])

    def test_missing_self_id_keeps_unknown_direction_and_source_limitations(self):
        export = self.base / "export-no-self"
        shutil.copytree(self.export, export)
        messages_path = export / "messages.jsonl"
        message = json.loads(messages_path.read_text(encoding="utf-8").splitlines()[0])
        message["senderId"] = "unidentified-person"
        message["direction"] = "unknown"
        messages_path.write_text(json.dumps(message, ensure_ascii=False) + "\n", encoding="utf-8")
        corpus_path = export / "analysis" / "corpus.jsonl"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8").splitlines()[0])
        corpus["senderId"] = "unidentified-person"
        corpus["direction"] = "unknown"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False) + "\n", encoding="utf-8")

        review_path = export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review.pop("selfId", None)
        review["sourceFingerprint"] = "synthetic-source-without-self"
        for item in review["reviewedArtifacts"]:
            if item.get("path") in {"messages.jsonl", "analysis/corpus.jsonl"}:
                target = export / item["path"]
                item["bytes"] = target.stat().st_size
                item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        review_path.write_text(json.dumps(review), encoding="utf-8")

        manifest_path = export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("selfId", None)
        manifest["sourceFingerprint"] = "synthetic-source-without-self"
        review_digest = hashlib.sha256(review_path.read_bytes()).hexdigest()
        for item in manifest["artifacts"]:
            if item.get("path") in {"messages.jsonl", "analysis/corpus.jsonl", "review.json"}:
                target = export / item["path"]
                item["bytes"] = target.stat().st_size
                item["sha256"] = (review_digest if item["path"] == "review.json"
                                   else hashlib.sha256(target.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        request = self.store.prepare(export)
        self.assertTrue(any("缺少可靠本人身份ID" in item for item in request["sourceLimitations"]))
        with self.assertRaises(ProfileError):
            self.store.stage(request["requestId"], self.candidate(request))
        uncertain = self.candidate(request)
        uncertain["observations"][0]["basis"] = "uncertain"
        staged = self.store.stage(request["requestId"], uncertain)
        loaded = self.store.candidate(staged["candidateId"])
        self.assertTrue(any("缺少可靠本人身份ID" in item for item in loaded["limitations"]))
        confirmed = self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        self.assertTrue(any("缺少可靠本人身份ID" in item for item in confirmed["limitations"]))

    def test_explicit_self_observation_cannot_use_only_peer_speech(self):
        request = self.store.prepare(self.export)
        candidate = self.candidate(request)
        candidate['observations'][0]['subject'] = 'self'
        with self.assertRaises(ProfileError):
            self.store.stage(request['requestId'], candidate)

    def test_storage_json_shapes_and_jsonl_fail_closed(self):
        for raw in ("[]", "null", '"text"'):
            path = self.base / "shape.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ProfileError):
                private_profiles._read_json(path)
        for raw in ("\ufeff{\"id\":\"m1\"}\n", "{\"id\":\"m1\"}\n\n", "[]\n", "{bad}\n"):
            path = self.base / "rows.jsonl"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ProfileError):
                private_profiles._load_lines(path)
        path = self.base / "large.jsonl"
        path.write_text("12345\n", encoding="utf-8")
        with patch.object(private_profiles, "_MAX_FILE_BYTES", 4):
            with self.assertRaises(ProfileError):
                private_profiles._load_lines(path)

    def test_parent_junctions_are_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows junction coverage")
        real = self.base / "real-root"
        real.mkdir()
        junction = self.base / "junction-root"
        # Exercise a real junction without an unrelated command-shell process.
        import _winapi
        _winapi.CreateJunction(str(real), str(junction))
        self.assertTrue(junction.is_junction())
        self.assertEqual(junction.resolve(), real.resolve())
        with self.assertRaises(ProfileError):
            ProfileStore(junction / "profiles")
        export_child = junction / "export"
        export_child.mkdir()
        with self.assertRaises(ProfileError):
            private_profiles._safe_inventory(export_child)

    def test_file_readers_bound_bytes_after_size_check(self):
        path = self.base / "growing.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        oversized = b"{}\n" + b"x" * 32
        class Reader:
            def __init__(self):
                self.read_sizes = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                self.read_sizes.append(size)
                return oversized

        for reader_func in (private_profiles._load_lines, private_profiles._read_json,
                            private_profiles._sha256_file):
            reader = Reader()
            with patch.object(Path, "open", return_value=reader):
                with patch.object(private_profiles, "_MAX_FILE_BYTES", 4):
                    with self.assertRaises(ProfileError):
                        reader_func(path)
            self.assertEqual(reader.read_sizes, [5])

    def test_deeply_nested_json_returns_profile_error(self):
        path = self.base / 'nested.json'
        path.write_text('{"bad":' + '[' * 3000 + '0' + ']' * 3000 + '}', encoding='utf8')
        for reader in (private_profiles._read_json, private_profiles._load_lines):
            with self.assertRaises(ProfileError):
                reader(path)

    def test_lock_does_not_reap_live_old_lock_and_releases_after_timeout(self):
        lock_path = self.store.root / ".confirm.lock"
        lock_path.write_text(str(os.getpid()), encoding="ascii")
        old = 1
        os.utime(lock_path, (old, old))
        lock = private_profiles._FileLock(self.store.root, "confirm")
        self.assertFalse(lock._stale())
        with patch.object(private_profiles.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaises(ProfileError):
                lock.__enter__()
        self.assertIsNone(lock.thread_lock)
        lock_path.unlink()
        with private_profiles._FileLock(self.store.root, "confirm"):
            self.assertTrue(lock_path.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows process handle semantics only")
    def test_windows_owner_alive_does_not_call_os_kill(self):
        path = self.base / "windows-live.lock"
        path.write_text(str(os.getpid()), encoding="ascii")
        with patch.object(private_profiles.os, "kill",
                          side_effect=AssertionError("os.kill must not be used on Windows")):
            self.assertTrue(private_profiles._FileLock._owner_alive(path))

    @unittest.skipUnless(os.name == "nt", "Windows process handle semantics only")
    def test_windows_owner_alive_dead_pid_and_unknown_permission_are_conservative(self):
        path = self.base / "windows-dead.lock"
        path.write_text("429496729", encoding="ascii")
        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = 0
        with patch.object(private_profiles.ctypes, "WinDLL", return_value=kernel32), \
             patch.object(private_profiles.ctypes, "get_last_error", return_value=87), \
             patch.object(private_profiles.os, "kill",
                          side_effect=AssertionError("os.kill must not be used on Windows")):
            self.assertFalse(private_profiles._FileLock._owner_alive(path))

        with patch.object(private_profiles.ctypes, "WinDLL", return_value=kernel32), \
             patch.object(private_profiles.ctypes, "get_last_error", return_value=5), \
             patch.object(private_profiles.os, "kill",
                          side_effect=AssertionError("os.kill must not be used on Windows")):
            self.assertTrue(private_profiles._FileLock._owner_alive(path))

        path.write_text(str(0x100000000), encoding="ascii")
        with patch.object(private_profiles.ctypes, "WinDLL",
                          side_effect=AssertionError("out-of-range PID must not reach Win32")), \
             patch.object(private_profiles.os, "kill",
                          side_effect=AssertionError("os.kill must not be used on Windows")):
            self.assertTrue(private_profiles._FileLock._owner_alive(path))

    def test_atomic_write_failure_cleans_temp_and_source_root_shape_is_error(self):
        target = self.base / "atomic.json"
        with patch.object(private_profiles.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(ProfileError):
                private_profiles._write_json(target, {"ok": True})
        self.assertEqual(list(self.base.glob(".atomic.json.*.tmp")), [])
        request = self.store.prepare(self.export)
        source_path = self.store.root / "sources" / (request["sources"][0]["sourceId"] + ".json")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["exportRoot"] = []
        source_path.write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaises(ProfileError):
            self.store.request(request["requestId"])

    def test_inspect_export_is_read_only_and_allows_no_analysis_material(self):
        inspected = self.store.inspect_export(self.export)
        self.assertEqual(inspected["identity"]["peerId"], "peer-1")
        self.assertEqual(inspected["reviewId"], "review-1")
        self.assertEqual(inspected["messages"][0]["id"], "m1")
        self.assertEqual(inspected["analysisRefs"], {"m1": 1})
        self.assertFalse(list((self.store.root / "requests").glob("*.json")))

        export = self.base / "export-no-analysis"
        shutil.copytree(self.export, export)
        for relative in ("analysis/corpus.jsonl", "analysis/index.json"):
            (export / relative).unlink()
        review_path = export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewedArtifacts"] = [item for item in review["reviewedArtifacts"]
                                       if item.get("path") not in {"analysis/corpus.jsonl", "analysis/index.json"}]
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest_path = export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = [item for item in manifest["artifacts"]
                                 if item.get("path") not in {"analysis/corpus.jsonl", "analysis/index.json"}]
        review_digest = hashlib.sha256(review_path.read_bytes()).hexdigest()
        for item in manifest["artifacts"]:
            if item.get("path") == "review.json":
                item["bytes"] = review_path.stat().st_size
                item["sha256"] = review_digest
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        no_analysis = self.store.inspect_export(export)
        self.assertEqual(no_analysis["messages"][0]["id"], "m1")
        self.assertEqual(no_analysis["analysisRefs"], {})

    def test_inspect_export_time_range_includes_non_analysis_messages(self):
        message_path = self.export / "messages.jsonl"
        message = {"id": "emoji-1", "ref": 2, "time": "2026-10-23T10:00:00+08:00",
                   "senderId": "peer-1", "direction": "incoming", "kind": "emoji",
                   "text": "[表情]"}
        message_path.write_text(message_path.read_text(encoding="utf-8") +
                                json.dumps(message, ensure_ascii=False) + "\n", encoding="utf-8")
        review_path = self.export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        message_bytes = message_path.read_bytes()
        for item in review["reviewedArtifacts"]:
            if item.get("path") == "messages.jsonl":
                item.update(bytes=len(message_bytes), sha256=hashlib.sha256(message_bytes).hexdigest())
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest_path = self.export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["artifacts"]:
            if item.get("path") == "messages.jsonl":
                item.update(bytes=len(message_bytes), sha256=hashlib.sha256(message_bytes).hexdigest())
            elif item.get("path") == "review.json":
                review_bytes = review_path.read_bytes()
                item.update(bytes=len(review_bytes), sha256=hashlib.sha256(review_bytes).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        inspected = self.store.inspect_export(self.export)
        self.assertEqual(inspected["timeRange"]["end"], message["time"])

    def test_legacy_analysis_time_range_source_remains_reopenable(self):
        message_path = self.export / "messages.jsonl"
        message = {"id": "emoji-legacy", "ref": 2, "time": "2026-10-23T10:00:00+08:00",
                   "senderId": "peer-1", "direction": "incoming", "kind": "emoji", "text": "[表情]"}
        message_path.write_text(message_path.read_text(encoding="utf-8") +
                                json.dumps(message, ensure_ascii=False) + "\n", encoding="utf-8")
        review_path = self.export / "review.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        for item in review["reviewedArtifacts"]:
            target = self.export / item["path"]
            item.update(bytes=target.stat().st_size,
                        sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        review_path.write_text(json.dumps(review), encoding="utf-8")
        manifest_path = self.export / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["artifacts"]:
            target = self.export / item["path"]
            if target.is_file():
                item.update(bytes=target.stat().st_size,
                            sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        request = self.store.prepare(self.export)
        staged = self.store.stage(request["requestId"], self.candidate(request))
        self.store.confirm(staged["candidateId"], confirmed_by="synthetic reviewer")
        source_path = self.store.root / "sources" / (request["sources"][0]["sourceId"] + ".json")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["timeRange"] = {"start": "2026-10-22T09:00:00+08:00",
                               "end": "2026-10-22T09:00:00+08:00"}
        source_path.write_text(json.dumps(source), encoding="utf-8")

        reopened = self.store.prepare(self.export)
        self.assertEqual(reopened["status"], "unchanged")
        self.assertEqual(reopened["sources"][0]["timeRange"], source["timeRange"])


if __name__ == "__main__":
    unittest.main()
