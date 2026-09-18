"""E3 來源、逐筆證據、完整候選與資料庫副本回歸；合成資料不是實板證據。"""

import contextlib
import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_lab as cli
import bpi_lab_metadata_review as review
import bpi_lab_prepare as preparation
from test_bpi_lab_special import fixture, UUID


class MetadataReviewTests(unittest.TestCase):
    def setUp(self):
        review.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="test-", dir=review.OUTPUT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = mock.patch.object(review, "OUTPUT_ROOT", self.root)
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.source = self.root / "sources"
        self.source.mkdir()
        entries = []
        for board, release, variant in sorted(review.MATRIX):
            directory = self.source / board
            directory.mkdir(exist_ok=True)
            name = f"Armbian-unofficial_26.11.0-trunk_{review.BOARDS[board].capitalize()}_{release}_legacy_0_{variant}.img.xz"
            path = directory / name
            blob = (board + release + variant).encode()
            path.write_bytes(blob)
            sha = hashlib.sha256(blob).hexdigest()
            path.with_name(name + ".sha").write_text(sha + "  " + name + "\n")
            with review.catalog_api._root(self.source) as fd:
                entry = review.catalog_api._entry(fd, board + "/" + name, path.stat())
            entry.update(architecture="arm32", family="sunplus-sp7021-bpi")
            entries.append(entry)
        self.targets = entries[:]
        for n in range(424):
            board = f"bpi-test{n % 43:02d}"
            entries.append({"image_id": f"other-{n:03d}", "relative_path": board + f"/image-{n}.img.xz",
                            "board": board, "artifact_board": "bananapitest", "kernel": "6.6.1",
                            "architecture": "arm64", "family": "test", "release": "bookworm",
                            "variant": "minimal", "issues": [], "identity": {}, "source_verified": False,
                            "expected_sha256": hashlib.sha256(str(n).encode()).hexdigest()})
        self.catalog = {"schema": "bpi-lab-catalog-v1", "root": str(self.source), "entries": entries,
                        "boards": [{"board": board, "issues": [p for e in entries if e["board"] == board for p in e["issues"]]}
                                   for board in sorted({e["board"] for e in entries})],
                        "issues": [p for e in entries for p in e["issues"]],
                        "hardware_validated": False, "metadata_only": True}
        self.catalog_ref = self.save("catalog.json", self.catalog)

    def save(self, name, data):
        return review.queue.save_json(self.root / name, data)

    def extraction(self, entry=None):
        entry = entry or self.targets[0]
        output = self.root / ("extraction-" + entry["image_id"])
        output.mkdir()
        files = fixture(entry["board"])
        index = {}
        for number, (path, blob) in enumerate(files.items()):
            name = f"file-{number:04d}.bin"
            (output / name).write_bytes(blob)
            index[path] = {"resolved": path, "links": [], "file": name, "digest": review.image.digest(blob)}
        queries = []
        for path in ("/usr/src", "/boot/extlinux", "/boot/boot.ini", "/boot/boot.scr.uimg", "/boot/boot.scr.local"):
            blob = (path + ": File not found by ext2_lookup\n").encode()
            name = f"query-{len(queries):04d}.stderr"
            (output / name).write_bytes(blob)
            queries.append({"command": "stat " + path, "returncode": 0, "stderr": review.image.digest(blob),
                            "stderr_file": name, "lookup_path": path})
        data = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                "hardware_validated": False, "source_kind": "xz", "mounted": False, "image_code_executed": False,
                "source": str(self.source / entry["relative_path"]),
                "source_digest": {"bytes": entry["compressed_bytes"], "sha256": entry["expected_sha256"]},
                "filesystem_uuid": UUID, "raw": {"bytes": 8 * 1024**2, "sha256": "a" * 64},
                "partition": {"index": 1, "start_lba": 2048, "sectors": 4096}, "files": index, "queries": queries}
        return review.queue.save_json(output / "extraction.json", data)

    def prepared(self, entry=None):
        entry = entry or self.targets[0]
        ext = self.extraction(entry)
        output = self.root / ("prepared-" + entry["image_id"])
        result = preparation.prepare(ext["path"], ext["sha256"], family="sunplus", board=entry["board"],
                                     kernel_release="0", output=output, from_extraction=True, layout="disk")
        self.assertEqual(result["status"], "prepared", result)
        return review.reference(output / "preparation.json")

    def verify(self, ref, entry=None):
        return review.verify_preparation(str(self.source), entry or self.targets[0], ref, self.root / "checked")

    def rewrite(self, ref, mutate):
        data = review.ref_data(ref)
        mutate(data)
        Path(ref["path"]).write_bytes(review.encode(data))
        return review.reference(ref["path"])

    def audits(self):
        def refs(entry):
            ref = {"path": str(self.root / entry["image_id"] / "synthetic.json"), "sha256": "a" * 64}
            return {**dict.fromkeys(("preparation", "extraction", "components", "revalidation"), ref),
                    "extraction_chain": [ref]}
        return [{**review.entry_binding(e), "status": "reviewed",
                 "evidence": {**refs(e), "kernel": "5.4.35-legacy-sunplus-sp7021-bpi", "binding": review.entry_binding(e),
                              "source_sha256": e["expected_sha256"], "source_identity": e["identity"],
                              "source_reread": False, "revalidation_source_reread": False,
                              "hardware_validated": False}} for e in self.targets]

    def candidate(self):
        return review.make_candidate(self.catalog, self.audits(), self.catalog_ref,
                                     {"path": str(self.root / "request.json"), "sha256": "a" * 64},
                                     "單元測試", "合成候選回歸",
                                     {"path": str(self.root / "review-record.json"), "sha256": "a" * 64})

    def verified_candidate(self):
        rows = [{**review.entry_binding(e), "preparation": self.prepared(e), "status": "prepared", "source_reread": False}
                for e in self.targets]
        request_ref = self.save("request.json", {"schema": "bpi-metadata-review-request-v1", "catalog": self.catalog_ref,
                                                "replays": None, "hardware_validated": False, "entries": rows})
        result = review.review(self.catalog_ref, request_ref, self.root / "review", reviewer="單元模型", reason="合成來源逐筆解析")
        self.assertEqual(result["status"], "candidate")
        return result["candidate"]

    def test_complete_catalog_and_source_guards(self):
        self.assertEqual(len(review.validate_catalog(self.catalog)), 20)
        for entry in self.targets:
            review.source_guard(str(self.source), entry)

    def test_subset_rejected(self):
        self.catalog["entries"] = self.targets
        with self.assertRaisesRegex(ValueError, "444"):
            review.validate_catalog(self.catalog)

    def test_unknown_extra_or_duplicate_issue_rejected(self):
        for issue in ({"code": "missing_sha256"}, self.targets[0]["issues"][0]):
            data = copy.deepcopy(self.catalog)
            data["entries"][0]["issues"].append(issue)
            with self.assertRaises(ValueError):
                review.validate_catalog(data)

    def test_nonzero_kernel_rejected(self):
        self.targets[0]["kernel"] = "5.4.35"
        with self.assertRaises(ValueError):
            review.validate_catalog(self.catalog)

    def test_board_os_role_family_architecture_mismatch_rejected(self):
        for field, value in (("board", "bpi-f3"), ("release", "noble"), ("variant", "xfce_desktop"),
                             ("artifact_board", "bananapif2s"), ("family", "other"), ("architecture", "arm64")):
            with self.subTest(field=field):
                data = copy.deepcopy(self.catalog)
                data["entries"][0][field] = value
                with self.assertRaises(ValueError):
                    review.validate_catalog(data)

    def test_other_board_issue_rejected(self):
        self.catalog["entries"][-1]["issues"] = [{"code": "unknown_kernel"}]
        with self.assertRaises(ValueError):
            review.validate_catalog(self.catalog)

    def test_top_and_board_unknown_issues_rejected(self):
        for where in ("issues", "boards"):
            data = copy.deepcopy(self.catalog)
            target = data["issues"] if where == "issues" else data["boards"][0]["issues"]
            target.append({"code": "unknown_extra"})
            with self.assertRaises(ValueError):
                review.validate_catalog(data)

    def test_duplicate_id_path_and_source_digest_rejected(self):
        for field in ("image_id", "relative_path", "expected_sha256"):
            data = copy.deepcopy(self.catalog)
            data["entries"][1][field] = data["entries"][0][field]
            with self.assertRaises(ValueError):
                review.validate_catalog(data)

    def test_changed_source_rejected(self):
        entry = self.targets[0]
        (self.source / entry["relative_path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            review.source_guard(str(self.source), entry)

    def test_changed_sidecar_rejected(self):
        entry = self.targets[0]
        (self.source / entry["sidecar"]["path"]).write_text("0" * 64 + "  " + Path(entry["relative_path"]).name + "\n")
        with self.assertRaises(ValueError):
            review.source_guard(str(self.source), entry)

    def test_source_symlink_rejected(self):
        entry = self.targets[0]
        path = self.source / entry["relative_path"]
        path.unlink()
        path.symlink_to(self.source / self.targets[1]["relative_path"])
        with self.assertRaises(ValueError):
            review.source_guard(str(self.source), entry)

    def test_pinned_json_digest_and_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            review.read_fixed(self.catalog_ref["path"], "0" * 64)
        path = self.root / "duplicate.json"
        path.write_bytes(b'{"a":1,"a":2}')
        with self.assertRaises(ValueError):
            review.ref_data(review.reference(path))

    def test_evidence_symlink_rejected(self):
        link = self.root / "linked.json"
        link.symlink_to(self.catalog_ref["path"])
        with self.assertRaises(ValueError):
            review.read_fixed(link, self.catalog_ref["sha256"])

    def test_output_outside_scope_and_overwrite_rejected(self):
        with self.assertRaises(ValueError):
            review.new_output(self.root.parent / "outside")
        review.new_output(self.root / "fresh")
        with self.assertRaises(FileExistsError):
            review.new_output(self.root / "fresh")

    def test_real_parser_replays_synthetic_binary_and_initrd(self):
        proof = self.verify(self.prepared())
        self.assertEqual(proof["kernel"], "5.4.35-legacy-sunplus-sp7021-bpi")
        self.assertFalse(proof["source_reread"])
        self.assertFalse(proof["revalidation_source_reread"])
        self.assertEqual(len(proof["extraction_chain"]), 2)

    def test_f2s_board_specific_dtb_replay(self):
        entry = next(x for x in self.targets if x["board"] == "bpi-f2s")
        self.assertEqual(self.verify(self.prepared(entry), entry)["binding"]["board"], "bpi-f2s")

    def test_preparation_cross_board_os_role_rejected(self):
        ref = self.prepared()
        for entry in (self.targets[1], self.targets[2], self.targets[10]):
            with self.subTest(entry=entry["relative_path"]), self.assertRaises(ValueError):
                self.verify(ref, entry)

    def test_extraction_digest_tamper_rejected(self):
        ref = self.prepared()
        data = review.ref_data(ref)
        path = Path(ref["path"]).parent / data["extraction"]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.verify(ref)

    def test_extraction_source_and_flags_rejected(self):
        for field, value in (("source", "/other/source.img.xz"), ("source_verified", False),
                             ("mounted", True), ("image_code_executed", True), ("source_kind", "raw")):
            with self.subTest(field=field):
                entry = self.targets[list(("source", "source_verified", "mounted", "image_code_executed", "source_kind")).index(field)]
                ext = self.extraction(entry)
                changed = self.rewrite(ext, lambda d: d.update({field: value}))
                with self.assertRaises(ValueError):
                    review.extraction_chain(changed, str(self.source), entry)

    def test_replay_cycle_rejected(self):
        ref = self.extraction()
        changed = self.rewrite(ref, lambda d: d.update(schema="bpi-lab-image-replay-v1", source_reread=False,
                                                      replay_of={"path": ref["path"], "sha256": "a" * 64}))
        with self.assertRaisesRegex(ValueError, "循環"):
            review.extraction_chain(changed, str(self.source), self.targets[0])

    def test_uncaptured_path_is_not_assumed_absent(self):
        ref = self.prepared()
        base = Path(ref["path"]).parent
        ext_path = base / "extraction/extraction.json"
        changed = self.rewrite(review.reference(ext_path), lambda d: d.update(queries=[]))
        ref = self.rewrite(ref, lambda d: d["extraction"].update(sha256=changed["sha256"]))
        with self.assertRaises(ValueError):
            self.verify(ref)

    def test_component_bytes_tamper_rejected(self):
        ref = self.prepared()
        base = Path(ref["path"]).parent
        ext = review.queue.read_json(base / "extraction/extraction.json")
        record = ext["files"]["/boot/bananapi/bpi-f2p/linux/uImage"]
        path = base / "extraction" / record["file"]
        path.write_bytes(b"not-a-kernel")
        with self.assertRaises(ValueError):
            self.verify(ref)

    def test_forged_kernel_preparation_rejected(self):
        ref = self.rewrite(self.prepared(), lambda d: d.update(kernel_release="6.6.999"))
        with self.assertRaises(ValueError):
            self.verify(ref)

    def test_forged_reread_flag_rejected(self):
        ref = self.rewrite(self.prepared(), lambda d: d.update(source_reread=True))
        with self.assertRaises(ValueError):
            self.verify(ref)

    def test_hardware_claim_and_preparation_path_escape_rejected(self):
        ref = self.rewrite(self.prepared(), lambda d: d.update(hardware_validated=True))
        with self.assertRaises(ValueError):
            self.verify(ref)
        ref = self.rewrite(ref, lambda d: (d.update(hardware_validated=False), d["extraction"].update(path="../extraction.json")))
        with self.assertRaises(ValueError):
            self.verify(ref)

    def request(self, single_sample=False):
        rows = []
        for entry in self.targets:
            rows.append({**review.entry_binding(entry), "status": "prepared", "source_reread": False,
                         "preparation": {"path": str(self.root / ("sample" if single_sample else entry["image_id"]) / "preparation.json"),
                                         "sha256": "a" * 64}})
        return {"schema": "bpi-metadata-review-request-v1", "catalog": self.catalog_ref,
                "replays": None, "hardware_validated": False, "entries": rows}

    def test_single_sample_batch_release_rejected(self):
        req = self.save("request.json", self.request(single_sample=True))
        with self.assertRaisesRegex(ValueError, "單一抽樣"):
            review.review(self.catalog_ref, req, self.root / "review", reviewer="測試", reason="合成回歸")

    def test_request_partial_or_os_mismatch_rejected(self):
        for name in ("partial", "os"):
            request = self.request()
            if name == "partial":
                request["entries"].pop()
            else:
                request["entries"][0]["release"] = "noble"
            req = self.save(name + ".json", request)
            with self.assertRaises(ValueError):
                review.review(self.catalog_ref, req, self.root / name, reviewer="測試", reason="合成回歸")

    def test_blocked_sources_never_emit_partial_candidate(self):
        request = self.request()
        for row in request["entries"]:
            row.update(status="blocked", error="合成來源阻擋")
        req = self.save("request.json", request)
        result = review.review(self.catalog_ref, req, self.root / "review", reviewer="測試", reason="合成回歸")
        self.assertEqual(result["blocked"], 20)
        self.assertIsNone(result["candidate"])
        self.assertFalse((self.root / "review/catalog-candidate.json").exists())

    def test_nineteen_passes_one_failure_never_emit_candidate(self):
        req = self.save("request.json", self.request())
        proofs = [x["evidence"] for x in self.audits()]
        proofs[-1] = ValueError("最後一筆來源阻擋")
        with mock.patch.object(review, "verify_preparation", side_effect=proofs):
            result = review.review(self.catalog_ref, req, self.root / "review", reviewer="測試", reason="合成回歸")
        self.assertEqual(result["reviewed"], 19)
        self.assertEqual(result["blocked"], 1)
        self.assertFalse((self.root / "review/catalog-candidate.json").exists())

    def test_candidate_preserves_444_ids_filenames_and_424_entries(self):
        candidate = self.candidate()
        self.assertEqual(candidate["entries"][20:], self.catalog["entries"][20:])
        self.assertEqual([e["image_id"] for e in candidate["entries"]], [e["image_id"] for e in self.catalog["entries"]])
        for old, new in zip(self.targets, candidate["entries"]):
            self.assertEqual(old["relative_path"], new["relative_path"])
            self.assertEqual(old["identity"], new["identity"])
            self.assertEqual(review.digest(old), new["metadata_review"]["original_entry_sha256"])
        self.assertFalse(candidate["metadata_review"]["integration_approved"])

    def test_candidate_scope_expansion_rejected(self):
        for field in ("relative_path", "image_id", "expected_sha256", "release", "variant"):
            candidate = self.candidate()
            candidate["entries"][0][field] = "changed"
            with self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)
        candidate = self.candidate()
        candidate["entries"][-1]["kernel"] = "9.9.9"
        with self.assertRaises(ValueError):
            review.validate_candidate(self.catalog, candidate)

    def test_candidate_trace_and_approval_tamper_rejected(self):
        for field, value in (("original_entry_sha256", "0" * 64), ("schema", "other")):
            candidate = self.candidate()
            candidate["entries"][0]["metadata_review"][field] = value
            with self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)
        candidate = self.candidate()
        candidate["entries"][0]["metadata_review"]["evidence"]["kernel"] = "9.9.9"
        with self.assertRaises(ValueError):
            review.validate_candidate(self.catalog, candidate)

    def test_kernel_only_change_rejected(self):
        candidate = self.candidate()
        candidate["entries"][0]["kernel"] = "6.6.999"
        with self.assertRaisesRegex(ValueError, "逐筆追溯"):
            review.validate_candidate(self.catalog, candidate)

    def test_trace_ref_identity_binding_reviewer_reason_and_flags_rejected(self):
        changes = {
            "catalog": {"path": str(self.root / "wrong.json"), "sha256": "0" * 64},
            "request": {"path": str(self.root / "wrong.json"), "sha256": "0" * 64},
            "review_record": {"path": str(self.root / "wrong.json"), "sha256": "0" * 64},
            "reviewer": "其他人", "reason": "其他理由", "integration_approved": True, "hardware_validated": True,
        }
        for field, value in changes.items():
            candidate = self.candidate()
            candidate["entries"][0]["metadata_review"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)

    def test_null_refs_and_nontext_reviewer_reason_rejected(self):
        for field, value in (("catalog", None), ("request", None), ("review_record", None),
                             ("reviewer", {}), ("reason", [])):
            candidate = self.candidate()
            candidate["metadata_review"][field] = value
            for entry in candidate["entries"][:20]:
                entry["metadata_review"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)

    def test_missing_preparation_extraction_component_revalidation_rejected(self):
        for field in ("preparation", "extraction", "components", "revalidation"):
            candidate = self.candidate()
            del candidate["entries"][0]["metadata_review"]["evidence"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)
        for field, value in (("source_sha256", "0" * 64), ("source_identity", {}), ("binding", {}),
                             ("hardware_validated", True), ("revalidation_source_reread", True)):
            candidate = self.candidate()
            candidate["entries"][0]["metadata_review"]["evidence"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)
        for where in ("metadata_review", "proof", "summary"):
            candidate = self.candidate()
            target = candidate["metadata_review"] if where == "summary" else candidate["entries"][0]["metadata_review"]
            if where == "proof":
                target = target["evidence"]
            target["extra"] = True
            with self.assertRaises(ValueError):
                review.validate_candidate(self.catalog, candidate)

    def test_consistently_forged_kernel_proof_record_still_rejected_by_binary(self):
        candidate_ref = self.verified_candidate()
        candidate = review.ref_data(candidate_ref)
        candidate["entries"][0]["kernel"] = "9.9.999"
        candidate["entries"][0]["metadata_review"]["evidence"]["kernel"] = "9.9.999"
        record = review.ref_data(candidate["metadata_review"]["review_record"])
        record["entries"][0]["evidence"]["kernel"] = "9.9.999"
        forged_ref = self.save("forged-record.json", record)
        candidate["metadata_review"]["review_record"] = forged_ref
        for entry in candidate["entries"][:20]:
            entry["metadata_review"]["review_record"] = forged_ref
        review.validate_candidate(self.catalog, candidate)
        with self.assertRaisesRegex(ValueError, "重新解析"):
            review.verify_candidate_evidence(self.catalog, candidate, self.catalog_ref, self.root / "audit")

    def test_pinned_preparation_component_and_revalidation_changes_rejected(self):
        candidate = review.ref_data(self.verified_candidate())
        proof = candidate["entries"][0]["metadata_review"]["evidence"]
        for index, field in enumerate(("preparation", "components", "revalidation")):
            path = Path(proof[field]["path"])
            blob = path.read_bytes()
            path.write_bytes(blob + b" ")
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.verify_candidate_evidence(self.catalog, candidate, self.catalog_ref, self.root / f"audit-{index}")
            path.write_bytes(blob)

    def test_fixed_catalog_request_record_mismatch_rejected(self):
        candidate = review.ref_data(self.verified_candidate())
        for index, field in enumerate(("catalog", "request", "review_record")):
            bad = copy.deepcopy(candidate)
            ref = {"path": str(self.root / "absent.json"), "sha256": "0" * 64}
            bad["metadata_review"][field] = ref
            for entry in bad["entries"][:20]:
                entry["metadata_review"][field] = ref
            with self.subTest(field=field), self.assertRaises((ValueError, OSError)):
                review.verify_candidate_evidence(self.catalog, bad, self.catalog_ref, self.root / f"audit-{index}")
        candidate = self.candidate()
        candidate["metadata_review"]["integration_approved"] = True
        with self.assertRaises(ValueError):
            review.validate_candidate(self.catalog, candidate)

    def database(self):
        source = self.root / "original.sqlite3"
        db = review.queue.connect(source)
        review.queue.import_catalog(db, self.catalog)
        for board in self.catalog["boards"]:
            station = cli.station_template(board["board"])
            review.queue.register_station(db, station)
            review.queue.schedule(db, station["station_id"])
        keys = [r[0] for r in db.execute("SELECT work_key FROM jobs WHERE state='queued' LIMIT 10")]
        db.executemany("UPDATE jobs SET state='review_required' WHERE work_key=?", [(k,) for k in keys])
        db.close()
        return source

    def test_temporary_database_preserves_old_work_and_disabled_stations(self):
        source = self.database()
        original_ref = review.reference(source)
        candidate_ref = self.verified_candidate()
        result = review.demonstrate(self.catalog_ref, candidate_ref, original_ref, self.root / "demo")
        self.assertEqual(result["unchanged_jobs"], 424)
        self.assertEqual(len(result["old_superseded"]), 20)
        self.assertEqual(len(result["new_queued"]), 20)
        self.assertEqual(result["disabled_stations"], 45)
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(review.reference(source), original_ref)
        self.assertFalse(Path(str(source) + "-wal").exists())

    def test_preexisting_job_tampered_hash_rejected(self):
        source = self.database()
        db = review.queue.connect(source)
        self.addCleanup(db.close)
        row = db.execute("SELECT work_key,body FROM jobs WHERE state='metadata_blocked' LIMIT 1").fetchone()
        body = review.queue.decode(row["body"])
        body["image_sha256"] = "0" * 64
        db.execute("UPDATE jobs SET body=? WHERE work_key=?", (review.encode(body).decode(), row["work_key"]))
        with self.assertRaisesRegex(ValueError, "固定工作鍵"):
            review.validate_job_snapshot(db, self.catalog)

    def test_self_consistent_forged_job_source_rejected_against_catalog(self):
        source = self.database()
        db = review.queue.connect(source)
        self.addCleanup(db.close)
        row = db.execute("SELECT work_key,body FROM jobs WHERE state='queued' LIMIT 1").fetchone()
        body = review.queue.decode(row["body"])
        body["image"]["expected_sha256"] = body["image_sha256"] = "0" * 64
        body["source_metadata_sha256"] = review.digest(body["image"])
        key = review.digest({f: body[f] for f in review.queue.BINDING_FIELDS})
        body["work_key"] = key
        db.execute("UPDATE jobs SET work_key=?,body=? WHERE work_key=?", (key, review.encode(body).decode(), row["work_key"]))
        review.queue.get_job(db, key)
        with self.assertRaisesRegex(ValueError, "不屬於原清單"):
            review.validate_job_snapshot(db, self.catalog)

    def test_job_source_root_and_station_contract_rejected(self):
        source = self.database()
        db = review.queue.connect(source)
        self.addCleanup(db.close)
        row = db.execute("SELECT work_key,body FROM jobs LIMIT 1").fetchone()
        original = review.queue.decode(row["body"])
        previous = row["work_key"]
        for field, value in (("image_root", "/different"), ("hardware_id", "different"),
                             ("boot_config_sha256", "9" * 64), ("test_version", "different")):
            body = copy.deepcopy(original)
            body[field] = value
            key = review.digest({f: body[f] for f in review.queue.BINDING_FIELDS})
            body["work_key"] = key
            db.execute("UPDATE jobs SET work_key=?,body=? WHERE work_key=?", (key, review.encode(body).decode(), previous))
            previous = key
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_job_snapshot(db, self.catalog)

    def test_self_consistent_simulation_job_on_hardware_station_rejected(self):
        source = self.database()
        db = review.queue.connect(source)
        self.addCleanup(db.close)
        row = db.execute("SELECT work_key,body FROM jobs WHERE state='metadata_blocked' LIMIT 1").fetchone()
        body = review.queue.decode(row["body"])
        self.assertEqual(body["station"]["mode"], "hardware")
        body["mode"] = "simulation"
        key = review.digest({field: body[field] for field in review.queue.BINDING_FIELDS})
        body["work_key"] = key
        db.execute("UPDATE jobs SET mode=?,work_key=?,body=? WHERE work_key=?",
                   ("simulation", key, review.encode(body).decode(), row["work_key"]))
        job = review.queue.get_job(db, key)
        self.assertEqual(job["mode"], "simulation")
        self.assertEqual(job["state"], "metadata_blocked")
        with self.assertRaisesRegex(ValueError, "工作模式必須與硬體站點一致"):
            review.validate_job_snapshot(db, self.catalog)

    def test_prepare_continues_source_failures_without_promotion(self):
        with mock.patch.object(review, "source_guard", side_effect=ValueError("來源已變更")), contextlib.redirect_stdout(io.StringIO()):
            result = review.prepare_sources(self.catalog_ref, self.root / "failed")
        self.assertEqual(len(result["entries"]), 20)
        self.assertTrue(all(x["status"] == "blocked" for x in result["entries"]))

    def test_complete_20_synthetic_preparation_review_pipeline(self):
        rows = [{"image_id": e["image_id"], "extraction": self.extraction(e)} for e in self.targets]
        replay_ref = self.save("replays.json", {"schema": "bpi-metadata-replays-v1", "entries": rows})
        with contextlib.redirect_stdout(io.StringIO()):
            prepared = review.prepare_sources(self.catalog_ref, self.root / "prepared", replay_ref)
        self.assertTrue(all(x["status"] == "prepared" for x in prepared["entries"]), prepared)
        request_ref = review.reference(self.root / "prepared/request.json")
        result = review.review(self.catalog_ref, request_ref, self.root / "review", reviewer="單元模型", reason="20 份各自合成來源")
        self.assertEqual(result["status"], "candidate", review.ref_data({"path": result["path"], "sha256": result["sha256"]}))
        review.validate_candidate(self.catalog, review.ref_data(result["candidate"]))


if __name__ == "__main__":
    unittest.main()
