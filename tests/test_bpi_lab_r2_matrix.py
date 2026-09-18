"""R2 固定十套來源與衍生候選回歸；只用小型合成資料，不代表實板證據。"""

import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_r2_matrix as r2
import bpi_lab_r2_repack as repack

matrix = r2.matrix
RELEASES = ("bookworm", "jammy", "noble", "resolute", "trixie")
VARIANTS = ("minimal", "xfce_desktop")
PREFIX = b"\x00\x01\x02\x03\x04"
SUFFIX = b"\xfa\xfb\xfc\xfd\xfe\xff"


def source_blob(release, variant):
    return ("合成來源：" + release + "/" + variant).encode()


def make_row(release="noble", variant="minimal"):
    token = hashlib.sha256((release + variant).encode()).hexdigest()
    root_uuid = f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:32]}"
    env = repack.OLD_ENV.replace(b"48136f54-b977-4c35-a2a9-25267664570a", root_uuid.encode())
    name = f"Armbian-unofficial_26.11.0-trunk_Bananapir2_{release}_current_6.6.153_{variant}"
    source = matrix.image.digest(source_blob(release, variant))
    entry = {"image_id": token, "board": "bpi-r2", "architecture": "arm32", "family": "mt7623",
             "release": release, "variant": variant, "branch": "current", "kernel": "6.6.153",
             "relative_path": "bpi-r2/" + name + ".img.xz", "expected_sha256": source["sha256"],
             "compressed_bytes": source["bytes"]}
    raw = PREFIX + env + SUFFIX
    return {"entry": entry, "root_uuid": root_uuid,
            "contract": {"source_sha256": source["sha256"], "raw_sha256": hashlib.sha256(raw).hexdigest(),
                         "raw_bytes": len(raw), "environment_hex": env.hex(), "name": name + "_i1-dtb-path.img"}}


def change_field(data, fields, value):
    target = data
    for field in fields[:-1]:
        target = target[field]
    target[fields[-1]] = value


class TemporaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch(repack.disk, "DiskReader", side_effect=AssertionError("測試不得開啟真映像讀取器"))
        self.patch(matrix.image, "bounded_run", side_effect=AssertionError("測試不得執行外部命令"))
        self.patch(matrix.preparation, "prepare", side_effect=AssertionError("測試不得執行真準備流程"))
        self.patch(matrix, "tool_binding", side_effect=AssertionError("測試不得掃描真工具或建置來源"))

    def patch(self, obj, name, *args, **kwargs):
        patcher = mock.patch.object(obj, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def json_ref(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(matrix.queue.encode(data))
        return r2.review.reference(path)

    def snapshot(self, directory, env, **fields):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "environment.bin").write_bytes(env)
        return self.json_ref(directory / "extraction.json", {
            "schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
            "hardware_validated": False, "mounted": False, "image_code_executed": False,
            "files": {"/boot/armbianEnv.txt": {"file": "environment.bin", "digest": matrix.image.digest(env)}},
            "queries": [], **fields})


class ContractTests(TemporaryTests):
    def test_ten_explicit_contracts_accept_distinct_environments_without_mutation(self):
        for release in RELEASES:
            for variant in VARIANTS:
                contract = make_row(release, variant)["contract"]
                before = copy.deepcopy(contract)
                with self.subTest(release=release, variant=variant):
                    self.assertEqual(repack.validate_contract(contract), bytes.fromhex(contract["environment_hex"]))
                    self.assertEqual(contract, before)

    def test_contract_requires_exact_fields_and_plain_dict(self):
        contract = make_row()["contract"]
        cases = [None, [], {**contract, "hardware_validated": False}]
        cases += [{key: value for key, value in contract.items() if key != missing} for missing in contract]
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "契約欄位"):
                repack.validate_contract(value)

    def test_contract_rejects_bad_digests_sizes_environment_encoding_and_names(self):
        base = make_row()["contract"]
        cases = [(key, value) for key in ("source_sha256", "raw_sha256")
                 for value in (None, True, "a" * 63, "b" * 65, "A" * 64, "g" * 64)]
        cases += [("raw_bytes", value) for value in (True, 0, -1, 1.5, "141", 8 * 1024**3 + 1)]
        cases += [("environment_hex", value) for value in
                  (None, b"00", "", "0", "xx", "aa " + base["environment_hex"], "AA", "61" * 4097)]
        cases += [("name", value) for value in (None, "../" + base["name"], "/" + base["name"],
                  base["name"].replace("noble", "unknown"), base["name"].replace("minimal", "server"),
                  base["name"].replace("6.6.153", "6.6.154"), base["name"].replace("current", "edge"),
                  base["name"].replace("Bananapir2", "Bananapir3"), base["name"] + ".xz")]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                repack.validate_contract({**base, field: value})

    def test_raw_size_upper_boundary_is_inclusive(self):
        contract = {**make_row()["contract"], "raw_bytes": 8 * 1024**3}
        self.assertEqual(repack.validate_contract(contract), bytes.fromhex(contract["environment_hex"]))

    def test_explicit_replacement_preserves_every_other_byte_and_inode_length(self):
        old_path = b"fdtfile=mediatek/mt7623n-bananapi-bpi-r2\n"
        new_path = b"fdtfile=mt7623n-bananapi-bpi-r2.dtb\n"
        for extra in (b"", b"console=serial\n", "# 測試環境\n".encode()):
            old = bytes.fromhex(make_row()["contract"]["environment_hex"]) + extra
            with self.subTest(extra=extra):
                new = repack.replacement(old, expected=old)
                self.assertEqual(new, old.replace(old_path, new_path) + b"#F3\n\n")
                self.assertEqual(len(new), len(old))
                self.assertEqual(new.count(b"fdtfile="), 1)
                description = f"Type: regular Mode: 0600 Flags: 0x80000\nLinks: 1\nSize: {len(old)}\n".encode()
                repack.validate_environment_inode(description, size=len(old))
                with self.assertRaises(ValueError):
                    repack.validate_environment_inode(description, size=len(old) + 1)
                with self.assertRaises(ValueError):
                    repack.replacement(old)

    def test_ambiguous_missing_oversized_and_unterminated_environment_rejected(self):
        old = repack.OLD_ENV
        cases = [b"", old.rstrip(b"\n"), old + b"fdtfile=another.dtb\n", old * 2,
                 old.replace(b"fdtfile=", b"other="), old.replace(b"bpi-r2\n", b"bpi-r2.dtb\n"),
                 old.replace(b"\n", b"\r\n"), old + b"#" * 4096 + b"\n"]
        for env in cases:
            with self.subTest(env=env[:100]), self.assertRaises(ValueError):
                repack.replacement(env, expected=env)
            with self.subTest(contract=env[:100]), self.assertRaises(ValueError):
                repack.validate_contract({**make_row()["contract"], "environment_hex": env.hex()})

    def test_contract_rejected_before_output_or_source_access(self):
        output = self.root / "candidate"
        with self.assertRaises(ValueError):
            repack.create_candidate(self.root / "absent.img.xz", output,
                                    contract={**make_row()["contract"], "raw_bytes": True})
        repack.disk.DiskReader.assert_not_called()
        self.assertFalse(output.exists())

    def test_candidate_creation_binds_source_and_rejects_wrong_raw_before_reading_env(self):
        contract = make_row()["contract"]
        reader = mock.MagicMock()
        reader.report = {"raw": {"bytes": contract["raw_bytes"], "sha256": "0" * 64}}
        factory = self.patch(repack.disk, "DiskReader")
        factory.return_value.__enter__.return_value = reader
        output, source = self.root / "candidate", self.root / "source.img.xz"
        with self.assertRaisesRegex(ValueError, "原始解壓摘要"):
            repack.create_candidate(source, output, contract=contract)
        factory.assert_called_once_with(source, contract["source_sha256"], output / "original-extraction",
                                        max_raw_bytes=contract["raw_bytes"], timeout=1800)
        reader.read_file.assert_not_called()
        matrix.image.bounded_run.assert_not_called()
        self.assertFalse((output / "candidate.json").exists())

    def test_wrong_environment_is_rejected_before_any_external_command(self):
        row = make_row()
        reader = mock.MagicMock()
        volume = object()
        reader.report = {"raw": {"bytes": row["contract"]["raw_bytes"], "sha256": row["contract"]["raw_sha256"]},
                         "partition": {"table": "dos"}}
        reader.volumes = [volume]
        reader.boot_volume = reader.root_volume = volume
        reader.read_file.return_value = repack.OLD_ENV
        factory = self.patch(repack.disk, "DiskReader")
        factory.return_value.__enter__.return_value = reader
        output = self.root / "candidate"
        with self.assertRaisesRegex(ValueError, "完整原環境"):
            repack.create_candidate(self.root / "source.img.xz", output, contract=row["contract"])
        reader.read_file.assert_called_once_with("/boot/armbianEnv.txt")
        matrix.image.bounded_run.assert_not_called()
        self.assertFalse((output / "candidate.json").exists())


class ReversalTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.old = bytes.fromhex(make_row()["contract"]["environment_hex"])
        self.new = repack.replacement(self.old, expected=self.old)
        self.path = self.root / "candidate.img"

    def test_entire_file_hashes_across_chunk_and_patch_boundaries_without_writes(self):
        for prefix, suffix in ((b"", SUFFIX), (PREFIX, SUFFIX), (PREFIX, b"")):
            original, candidate = prefix + self.old + suffix, prefix + self.new + suffix
            self.path.write_bytes(candidate)
            for chunk in (1, 7, len(self.old), 1024**2):
                with self.subTest(prefix=prefix, suffix=suffix, chunk=chunk), self.path.open("rb") as stream:
                    check = mock.Mock()
                    stream.seek(3)
                    with mock.patch.object(matrix.image, "CHUNK", chunk):
                        result = repack.verify_reversal(stream.fileno(), len(candidate), len(prefix),
                                                       self.old, self.new, check)
                    self.assertEqual(result, {"bytes": len(candidate),
                                             "original_sha256": hashlib.sha256(original).hexdigest(),
                                             "candidate_sha256": hashlib.sha256(candidate).hexdigest()})
                    self.assertEqual(check.call_count, (len(candidate) + chunk - 1) // chunk)
                    self.assertEqual(stream.tell(), 3)
                    self.assertEqual(self.path.read_bytes(), candidate)

    def test_changes_outside_environment_cannot_reproduce_original_digest(self):
        candidate = PREFIX + self.new + SUFFIX
        expected = hashlib.sha256(PREFIX + self.old + SUFFIX).hexdigest()
        for offset in (0, len(candidate) - 1):
            changed = bytearray(candidate)
            changed[offset] ^= 1
            self.path.write_bytes(changed)
            with self.subTest(offset=offset), self.path.open("rb") as stream:
                result = repack.verify_reversal(stream.fileno(), len(changed), len(PREFIX), self.old, self.new)
                self.assertNotEqual(result["original_sha256"], expected)
                self.assertEqual(result["candidate_sha256"], hashlib.sha256(changed).hexdigest())

    def test_invalid_ranges_wrong_mapping_truncation_and_deadline_are_not_success(self):
        candidate = PREFIX + self.new + SUFFIX
        self.path.write_bytes(candidate)
        cases = [(True, len(PREFIX), self.old, self.new), (len(candidate), True, self.old, self.new),
                 (len(candidate), -1, self.old, self.new), (len(candidate), len(candidate), self.old, self.new),
                 (len(candidate), len(PREFIX), self.old[:-1], self.new),
                 (len(candidate), len(PREFIX), b"", b""),
                 (len(candidate), 0, self.old, self.new)]
        with self.path.open("rb") as stream:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    repack.verify_reversal(stream.fileno(), *args)
            with self.assertRaisesRegex(ValueError, "截斷"):
                repack.verify_reversal(stream.fileno(), len(candidate) + 1, len(PREFIX), self.old, self.new)
            with self.assertRaisesRegex(ValueError, "測試期限"):
                repack.verify_reversal(stream.fileno(), len(candidate), len(PREFIX), self.old, self.new,
                                       mock.Mock(side_effect=ValueError("測試期限")))
        self.path.write_bytes(PREFIX + self.old + SUFFIX)
        with self.path.open("rb") as stream, self.assertRaisesRegex(ValueError, "修改區間讀回"):
            repack.verify_reversal(stream.fileno(), len(candidate), len(PREFIX), self.old, self.new)


class SourceRowsTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.rows = [make_row(release, variant) for release in RELEASES for variant in VARIANTS]
        self.catalog = {"root": str(self.root / "sources"), "entries": [row["entry"] for row in self.rows]}
        self.documents = {}
        self.receipts, self.plans, audit_rows = [], [], []
        for number, row in enumerate(self.rows):
            directory = self.root / f"original-{number}" / "attempt-0001" / "source"
            env = bytes.fromhex(row["contract"]["environment_hex"])
            extraction = self.snapshot(directory / "extraction", env)
            data = r2.review.ref_data(extraction)
            data.update(schema="bpi-lab-image-v1", ok=True, source_verified=True, hardware_validated=False,
                        mounted=False, image_code_executed=False, source_kind="xz",
                        source=str(Path(self.catalog["root"]) / row["entry"]["relative_path"]),
                        source_digest={"bytes": row["entry"]["compressed_bytes"],
                                       "sha256": row["entry"]["expected_sha256"]},
                        raw={"bytes": row["contract"]["raw_bytes"], "sha256": row["contract"]["raw_sha256"]},
                        filesystem_uuid=row["root_uuid"])
            self.json_ref(Path(extraction["path"]), data)
            plan = {"rows": [{"entry": row["entry"]}]}
            plan_ref = self.document(f"plan-{number}", plan)
            receipt = {"status": "blocked", "kernel_release": repack.RELEASE,
                       "image_id": row["entry"]["image_id"], "plan": plan_ref,
                       "attempts": [{"preparation": {"path": str(directory / "preparation.json"), "sha256": "a" * 64}}]}
            receipt_ref = self.document(f"receipt-{number}", receipt)
            audit_rows.append({"board": "bpi-r2", "image_id": row["entry"]["image_id"],
                               "source_sha256": row["entry"]["expected_sha256"], "receipt": receipt_ref})
            self.receipts.append(receipt)
            self.plans.append(plan)
        self.audit = {"catalog": self.document("catalog", self.catalog), "rows": list(reversed(audit_rows))}
        self.audit["rows"].append({"board": "bpi-r3", "image_id": "ignored"})
        audit_ref = self.document("audit", self.audit)
        self.patch(r2, "AUDIT", audit_ref)
        original_ref_data = r2.review.ref_data
        self.patch(r2.review, "ref_data", side_effect=lambda ref: self.documents[ref["path"]]
                   if ref["path"] in self.documents else original_ref_data(ref))
        # 完整清單與舊收據另有回歸；此處保留真實擷取鏈及小檔摘要核對。
        self.validate_catalog = self.patch(r2.review, "validate_catalog")
        self.verify_completed = self.patch(matrix, "verify_completed")

    def document(self, name, value):
        path = str(self.root / (name + ".json"))
        self.documents[path] = value
        return {"path": path, "sha256": matrix.queue.digest(value)}

    def test_exact_ten_sources_sorted_and_each_contract_bound_to_own_extraction(self):
        root, rows = r2.source_rows()
        self.assertEqual(root, self.catalog["root"])
        self.assertEqual([(row["entry"]["release"], row["entry"]["variant"]) for row in rows],
                         [(release, variant) for release in RELEASES for variant in VARIANTS])
        self.assertEqual(len({row["contract"]["source_sha256"] for row in rows}), 10)
        self.validate_catalog.assert_called_once_with(self.catalog)
        self.assertEqual(self.verify_completed.call_count, 10)
        for row, expected in zip(rows, self.rows):
            self.assertEqual(row["entry"], expected["entry"])
            self.assertEqual(row["contract"], expected["contract"])
            self.assertEqual(row["root_uuid"], expected["root_uuid"])
            self.assertIn(row["original_receipt"], [item["receipt"] for item in self.audit["rows"][:-1]])
            self.assertTrue(Path(row["original_extraction"]["path"]).is_relative_to(self.root))

    def test_missing_extra_duplicate_and_unknown_source_combinations_rejected(self):
        original = copy.deepcopy(self.audit["rows"])
        for mode in ("missing", "extra", "duplicate"):
            self.audit["rows"] = copy.deepcopy(original)
            if mode == "missing":
                self.audit["rows"].pop(0)
            elif mode == "extra":
                self.audit["rows"].append(copy.deepcopy(self.audit["rows"][0]))
            else:
                self.audit["rows"][1] = copy.deepcopy(self.audit["rows"][0])
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "十套矩陣"):
                r2.source_rows()
        self.audit["rows"] = original
        self.catalog["entries"][0]["release"] = "unknown"
        with self.assertRaisesRegex(ValueError, "十套矩陣"):
            r2.source_rows()

    def test_wrong_board_architecture_kernel_branch_and_source_digest_rejected(self):
        entry = self.catalog["entries"][0]
        for field, value in (("board", "bpi-r3"), ("architecture", "arm64"), ("kernel", "6.6.154"),
                             ("branch", "edge"), ("expected_sha256", "0" * 64)):
            old = entry[field]
            entry[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "原來源錯配"):
                r2.source_rows()
            entry[field] = old

    def test_receipt_and_plan_must_match_exact_original_entry(self):
        receipt = self.receipts[0]
        for field, value in (("status", "prepared"), ("kernel_release", "6.6.154-current-mt7623"),
                             ("image_id", self.rows[1]["entry"]["image_id"])):
            old = receipt[field]
            receipt[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "原 R2 收據"):
                r2.source_rows()
            receipt[field] = old
        original = self.plans[0]["rows"]
        for rows in ([], original * 2, [{"entry": self.rows[1]["entry"]}]):
            self.plans[0]["rows"] = rows
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "來源不唯一"):
                r2.source_rows()

    def test_catalog_and_old_receipt_validation_failures_propagate(self):
        for boundary in (self.validate_catalog, self.verify_completed):
            boundary.side_effect = ValueError("測試固定證據錯配")
            with self.subTest(boundary=boundary), self.assertRaisesRegex(ValueError, "固定證據錯配"):
                r2.source_rows()
            boundary.side_effect = None

    def test_original_environment_tampering_is_not_silently_reread(self):
        path = self.root / "original-0/attempt-0001/source/extraction/environment.bin"
        path.write_bytes(path.read_bytes().replace(b"verbosity=1", b"verbosity=8"))
        with self.assertRaisesRegex(ValueError, "擷取組件已改變"):
            r2.source_rows()

    def test_original_extraction_cannot_be_rebound_to_another_source(self):
        path = self.root / "original-0/attempt-0001/source/extraction/extraction.json"
        original = matrix.queue.read_json(path)
        for fields, value in ((('source_digest', 'sha256'), self.rows[1]["entry"]["expected_sha256"]),
                              (('source_digest', 'bytes'), 1), (('source',), str(self.root / "other.img.xz")),
                              (('source_kind',), "raw"), (('hardware_validated',), True)):
            data = copy.deepcopy(original)
            change_field(data, fields, value)
            self.json_ref(path, data)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                r2.source_rows()


class CandidateFixture(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.row = make_row()
        self.source_root = self.root / "sources"
        source = self.source_root / self.row["entry"]["relative_path"]
        source.parent.mkdir(parents=True)
        source.write_bytes(source_blob("noble", "minimal"))
        self.sidecar = source.with_name(source.name + ".sha")
        self.sidecar.write_text(self.row["entry"]["expected_sha256"] + "  " + source.name + "\n")
        with matrix.catalog_api._root(self.source_root) as fd:
            entry = matrix.catalog_api._entry(fd, self.row["entry"]["relative_path"], source.stat())
        self.row["entry"].update(entry)
        self.output = self.root / "evidence/batch"
        self.directory = self.output / "jobs" / self.row["entry"]["image_id"]
        self.attempt = self.directory / "attempt-0001"
        self.candidate_dir = self.attempt / "candidate"
        self.candidate_dir.mkdir(parents=True)
        self.old = bytes.fromhex(self.row["contract"]["environment_hex"])
        self.new = repack.replacement(self.old, expected=self.old)
        self.raw_path = self.candidate_dir / self.row["contract"]["name"]
        self.raw_path.write_bytes(PREFIX + self.new + SUFFIX)
        self.xz_path = self.raw_path.with_name(self.raw_path.name + ".xz")
        self.xz_path.write_bytes("合成壓縮產物；不解壓縮".encode())
        self.extraction(self.candidate_dir / "original-extraction", self.old, source, "xz",
                        {"bytes": self.row["contract"]["raw_bytes"], "sha256": self.row["contract"]["raw_sha256"]})
        self.extraction(self.candidate_dir / "candidate-extraction", self.new, self.raw_path, "raw",
                        matrix.file_digest(self.raw_path))
        self.candidate = {
            "schema": "bpi-r2-derived-candidate-v1", "hardware_validated": False,
            "rebuilt": False, "source_modified": False, "original_source_still_blocked": True,
            "family_prepared": True, "e2fsck_exit_code": 0, "internal_only": True,
            "integration_approved": False, "boot_blob_redistribution_authorized": False,
            "source": {"path": str(source), "sha256": self.row["entry"]["expected_sha256"]},
            "candidate": {"path": str(self.xz_path), **matrix.file_digest(self.xz_path)},
            "raw": {**matrix.file_digest(self.raw_path), "original_sha256": self.row["contract"]["raw_sha256"]},
            "changed_range": {"offset": len(PREFIX), "bytes": len(self.old),
                              "before": matrix.image.digest(self.old), "after": matrix.image.digest(self.new)}}
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.build_tool = self.repository / "builder.py"
        self.build_tool.write_bytes(b"VALUE = 1\n")
        self.patch(matrix, "ROOT", self.repository)
        self.final = self.attempt / "final"
        extraction = self.extraction(self.final / "extraction", self.new, self.xz_path, "xz",
                                     matrix.file_digest(self.raw_path))
        self.json_ref(self.final / "family-result.json", {
            "board": "bpi-r2", "sources": {"builder.py": matrix.file_digest(self.build_tool)}})
        self.preparation = {
            "schema": "bpi-lab-prepare-v1", "status": "prepared", "blockers": [],
            "family": "mediatek", "board": "bpi-r2", "kernel_release": repack.RELEASE,
            "source": matrix.file_digest(self.xz_path), "raw": matrix.file_digest(self.raw_path),
            "root_uuid": self.row["root_uuid"], "root_uuid_verified": True, "source_reread": True,
            "hardware_validated": False, "whole_backend_ready": False, "media_written": False,
            "boot_executed": False,
            "components": {"path": "family-result.json", **matrix.file_digest(self.final / "family-result.json")},
            "extraction": {"path": "extraction/extraction.json", **matrix.file_digest(Path(extraction["path"]))}}
        self.plan = {"source_root": str(self.source_root), "rows": [self.row]}
        self.plan_ref = self.json_ref(self.output / "plan.json", self.plan)
        self.refresh_refs()
        self.patch(sys, "stdout", io.StringIO())

    def extraction(self, directory, env, source, source_kind, raw):
        return self.snapshot(directory, env, source=str(source), source_kind=source_kind,
                             source_digest=matrix.file_digest(source), raw=raw, filesystem_uuid=self.row["root_uuid"])

    def refresh_refs(self):
        self.candidate_ref = self.json_ref(self.candidate_dir / "candidate.json", self.candidate)
        self.preparation_ref = self.json_ref(self.final / "preparation.json", self.preparation)

    def verify(self):
        self.refresh_refs()
        return r2.verify_candidate(self.candidate_ref, self.preparation_ref, self.row, str(self.source_root))

    def expected_verified(self):
        return {"candidate": self.candidate["candidate"], "raw": self.candidate["raw"],
                "changed_range": self.candidate["changed_range"], "root_uuid": self.row["root_uuid"],
                "inverse_verification": {"bytes": len(PREFIX + self.old + SUFFIX),
                                         "original_sha256": hashlib.sha256(PREFIX + self.old + SUFFIX).hexdigest(),
                                         "candidate_sha256": hashlib.sha256(PREFIX + self.new + SUFFIX).hexdigest()}}

    def receipt(self):
        return {"schema": "bpi-r2-matrix-result-v1", "plan": self.plan_ref,
                "row_sha256": matrix.queue.digest(self.row), "status": "prepared",
                "image_id": self.row["entry"]["image_id"], "release": self.row["entry"]["release"],
                "variant": self.row["entry"]["variant"], "source_sha256": self.row["entry"]["expected_sha256"],
                "internal_only": True, "hardware_validated": False, "source_modified": False,
                "boot_blob_redistribution_authorized": False, "reused_legacy": False,
                "attempt": str(self.attempt), "candidate_manifest": self.candidate_ref,
                "preparation": self.preparation_ref, "artifacts": matrix.artifact_digest(self.attempt),
                "verified": self.expected_verified()}

    def execute(self):
        return r2.execute_row(self.plan_ref, self.plan, self.row, self.output)


class CandidateTests(CandidateFixture):
    def test_source_only_and_full_candidate_verification_compute_real_tiny_file_digests(self):
        candidate, actual, verified = r2.verify_candidate_source(
            self.candidate_ref, self.row, str(self.source_root))
        self.assertEqual(candidate, self.candidate)
        self.assertEqual(actual, matrix.file_digest(self.xz_path))
        self.assertEqual(verified, self.expected_verified())
        self.assertEqual(self.verify(), self.expected_verified())

    def test_candidate_source_raw_environment_and_scope_mismatches_rejected(self):
        cases = [(('schema',), "unknown"), (('hardware_validated',), True), (('rebuilt',), True),
                 (('source_modified',), True), (('original_source_still_blocked',), False),
                 (('family_prepared',), False), (('e2fsck_exit_code',), 1),
                 (('boot_blob_redistribution_authorized',), True),
                 (('source', 'path'), str(self.source_root / "other.img.xz")),
                 (('source', 'sha256'), "0" * 64), (('raw', 'original_sha256'), "0" * 64),
                 (('raw', 'sha256'), "0" * 64), (('raw', 'bytes'), len(PREFIX + self.old + SUFFIX) + 1),
                 (('changed_range', 'bytes'), len(self.old) - 1),
                 (('changed_range', 'offset'), len(PREFIX) + 1),
                 (('changed_range', 'before'), matrix.image.digest(b"0")),
                 (('changed_range', 'after'), matrix.image.digest(b"0")),
                 (('candidate', 'bytes'), 1), (('candidate', 'sha256'), "0" * 64),
                 (('candidate', 'path'), str(self.root / self.xz_path.name)),
                 (('candidate', 'path'), str(self.raw_path))]
        original = copy.deepcopy(self.candidate)
        for fields, value in cases:
            self.candidate = copy.deepcopy(original)
            change_field(self.candidate, fields, value)
            self.refresh_refs()
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                r2.verify_candidate_source(self.candidate_ref, self.row, str(self.source_root))

    def test_candidate_cannot_claim_public_or_integrated_approval(self):
        original = copy.deepcopy(self.candidate)
        for field, value in (("internal_only", False), ("integration_approved", True)):
            self.candidate = copy.deepcopy(original)
            self.candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()

    def test_new_candidate_must_explicitly_deny_publication_and_integration(self):
        original = copy.deepcopy(self.candidate)
        for field in ("internal_only", "integration_approved", "boot_blob_redistribution_authorized"):
            self.candidate = copy.deepcopy(original)
            self.candidate.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()

    def test_only_exact_pinned_legacy_reference_can_omit_new_restriction_fields(self):
        for field in ("internal_only", "integration_approved", "boot_blob_redistribution_authorized"):
            self.candidate.pop(field)
        self.refresh_refs()
        legacy_ref = dict(self.candidate_ref)
        with mock.patch.object(r2, "LEGACY_CANDIDATE", legacy_ref):
            self.assertEqual(self.verify(), self.expected_verified())
        for field, value in (("path", str(self.root / "other/candidate.json")), ("sha256", "0" * 64)):
            with self.subTest(field=field), mock.patch.object(r2, "LEGACY_CANDIDATE", {**legacy_ref, field: value}):
                with self.assertRaises(ValueError):
                    self.verify()

    def test_legacy_exception_does_not_allow_explicit_publication_or_integration(self):
        original = copy.deepcopy(self.candidate)
        for field, value in (("internal_only", False), ("integration_approved", True),
                             ("boot_blob_redistribution_authorized", True)):
            self.candidate = {**original, field: value}
            self.refresh_refs()
            with self.subTest(field=field), mock.patch.object(r2, "LEGACY_CANDIDATE", dict(self.candidate_ref)):
                with self.assertRaises(ValueError):
                    self.verify()

    def test_final_extraction_source_and_raw_must_match_even_after_rehashing_metadata(self):
        path = self.final / "extraction/extraction.json"
        original = matrix.queue.read_json(path)
        for name in ("source_digest", "raw"):
            for field, value in (("sha256", "0" * 64), ("bytes", 1)):
                data = copy.deepcopy(original)
                data[name][field] = value
                self.json_ref(path, data)
                self.preparation["extraction"].update(matrix.file_digest(path))
                with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                    self.verify()

    def test_each_extraction_requires_own_source_raw_root_and_readonly_identity(self):
        paths = [self.candidate_dir / name / "extraction.json"
                 for name in ("original-extraction", "candidate-extraction")]
        paths.append(self.final / "extraction/extraction.json")
        for path in paths:
            original = matrix.queue.read_json(path)
            cases = [(('schema',), "unknown"), (('ok',), False), (('source_verified',), False),
                     (('hardware_validated',), True), (('mounted',), True), (('image_code_executed',), True),
                     (('source',), str(self.root / "unrelated.img.xz")),
                     (('source_kind',), "raw" if original["source_kind"] == "xz" else "xz"),
                     (('source_digest', 'sha256'), "0" * 64), (('source_digest', 'bytes'), 1),
                     (('raw', 'sha256'), "0" * 64), (('raw', 'bytes'), 1),
                     (('filesystem_uuid',), make_row("trixie")["root_uuid"])]
            for fields, value in cases:
                data = copy.deepcopy(original)
                change_field(data, fields, value)
                self.json_ref(path, data)
                if path.parent.parent == self.final:
                    self.preparation["extraction"].update(matrix.file_digest(path))
                with self.subTest(path=path, fields=fields), self.assertRaises(ValueError):
                    self.verify()
            self.json_ref(path, original)
            if path.parent.parent == self.final:
                self.preparation["extraction"].update(matrix.file_digest(path))

    def test_original_xz_cannot_masquerade_as_derived_candidate(self):
        self.xz_path.write_bytes(source_blob("noble", "minimal"))
        self.candidate["candidate"].update(matrix.file_digest(self.xz_path))
        self.preparation["source"] = matrix.file_digest(self.xz_path)
        path = self.final / "extraction/extraction.json"
        data = matrix.queue.read_json(path)
        data["source_digest"] = matrix.file_digest(self.xz_path)
        self.json_ref(path, data)
        self.preparation["extraction"].update(matrix.file_digest(path))
        self.assertEqual(self.candidate["candidate"]["sha256"], self.row["entry"]["expected_sha256"])
        with self.assertRaises(ValueError):
            self.verify()

    def test_preparation_identity_source_root_and_safety_flags_must_all_match(self):
        cases = [(('schema',), "unknown"), (('status',), "blocked"), (('blockers',), ["測試阻擋"]),
                 (('family',), "allwinner"), (('board',), "bpi-r3"),
                 (('kernel_release',), "6.6.154-current-mt7623"), (('source', 'sha256'), "0" * 64),
                 (('source', 'bytes'), 1), (('raw', 'sha256'), "0" * 64), (('raw', 'bytes'), 1),
                 (('root_uuid',), make_row("trixie")["root_uuid"]), (('root_uuid_verified',), False),
                 (('source_reread',), False), (('hardware_validated',), True),
                 (('whole_backend_ready',), True), (('media_written',), True), (('boot_executed',), True)]
        original = copy.deepcopy(self.preparation)
        for fields, value in cases:
            self.preparation = copy.deepcopy(original)
            change_field(self.preparation, fields, value)
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "固定來源與根身分"):
                self.verify()

    def test_preparation_component_paths_and_digests_cannot_be_substituted(self):
        original = copy.deepcopy(self.preparation)
        for name in ("components", "extraction"):
            for field, value in (("path", "../family-result.json"), ("path", "/tmp/extraction.json"),
                                 ("sha256", "0" * 64), ("bytes", 0)):
                self.preparation = copy.deepcopy(original)
                self.preparation[name][field] = value
                with self.subTest(name=name, field=field, value=value), self.assertRaises(ValueError):
                    self.verify()

    def test_changed_compressed_raw_and_environment_data_rejected(self):
        for path, blob, message in (
                (self.xz_path, b"\x01", "候選 XZ"),
                (self.raw_path, self.raw_path.read_bytes() + b"\x00", "raw 大小"),
                (self.raw_path, PREFIX + self.old + SUFFIX, "修改區間讀回"),
                (self.raw_path, b"\x01" + PREFIX[1:] + self.new + SUFFIX, "完整逆向"),
                (self.raw_path, PREFIX + self.new + SUFFIX[:-1] + b"\x00", "完整逆向")):
            before = path.read_bytes()
            path.write_bytes(blob)
            with self.subTest(path=path, message=message), self.assertRaisesRegex(ValueError, message):
                self.verify()
            path.write_bytes(before)

    def test_wrong_row_environment_rejected_even_with_same_length(self):
        self.row["contract"]["environment_hex"] = make_row("trixie")["contract"]["environment_hex"]
        with self.assertRaisesRegex(ValueError, "環境與原來源契約"):
            self.verify()

    def test_snapshots_build_tools_and_original_source_sidecar_are_rechecked(self):
        cases = [(self.candidate_dir / name / "environment.bin", "擷取組件已改變")
                 for name in ("original-extraction", "candidate-extraction")]
        cases += [(self.final / "extraction/environment.bin", "擷取組件已改變"),
                  (self.build_tool, "建置來源已變動"), (self.sidecar, "旁檔已變動")]
        for path, message in cases:
            before = path.read_bytes()
            changed = before.replace(self.row["entry"]["expected_sha256"].encode(), b"0" * 64) \
                if path == self.sidecar else b"\x01"
            path.write_bytes(changed)
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, message):
                self.verify()
            path.write_bytes(before)


class ReceiptAndResumeTests(CandidateFixture):
    def test_receipt_resume_rechecks_all_evidence_without_rebuild_or_republication(self):
        receipt = self.receipt()
        path = self.directory / "receipt.json"
        self.json_ref(path, receipt)
        before, identity = path.read_bytes(), path.stat()
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("續作不得重建"))
        publish = self.patch(matrix, "publish_json", side_effect=AssertionError("續作不得重新發布"))
        self.assertEqual(self.execute(), receipt)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), (identity.st_ino, identity.st_mtime_ns))
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()
        publish.assert_not_called()

    def test_receipt_binding_flags_summary_and_artifacts_must_match(self):
        original = self.receipt()
        cases = [(('schema',), "unknown"), (('plan', 'sha256'), "0" * 64), (('row_sha256',), "0" * 64),
                 (('status',), "blocked"), (('internal_only',), False), (('hardware_validated',), True),
                 (('source_modified',), True), (('boot_blob_redistribution_authorized',), True),
                 (('reused_legacy',), 1), (('reused_legacy',), True),
                 (('artifacts', 'sha256'), "0" * 64), (('verified', 'root_uuid'), "unknown")]
        for fields, value in cases:
            receipt = copy.deepcopy(original)
            change_field(receipt, fields, value)
            with self.subTest(fields=fields, value=value), self.assertRaises(ValueError):
                r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory)

    def test_receipt_descriptive_identity_cannot_disagree_with_bound_row(self):
        original = self.receipt()
        for field, value in (("image_id", "0" * 64), ("release", "trixie"),
                             ("variant", "xfce_desktop"), ("source_sha256", "0" * 64)):
            receipt = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory)

    def test_receipt_requires_exact_attempt_candidate_and_final_paths(self):
        original = self.receipt()
        cases = [(('attempt',), str(self.root / "attempt-0001")),
                 (('candidate_manifest', 'path'), str(self.attempt / "other/candidate.json"))]
        cases += [(('preparation', 'path'), str(self.attempt / name)) for name in (
            "final-extra/preparation.json", "final-002/preparation.json", "final-00002/preparation.json",
            "final/nested/preparation.json", "../final/preparation.json", "final/other.json")]
        for fields, value in cases:
            receipt = copy.deepcopy(original)
            change_field(receipt, fields, value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "產物或路徑"):
                r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory)

    def test_numbered_final_preparation_is_valid_inside_exact_attempt(self):
        new_final = self.attempt / "final-0002"
        self.final.rename(new_final)
        self.final = new_final
        self.refresh_refs()
        receipt = self.receipt()
        self.assertEqual(r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory), receipt)

    def test_completed_but_unpublished_candidate_and_preparation_are_reused(self):
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))
        source_check = self.patch(r2, "verify_candidate_source", wraps=r2.verify_candidate_source)
        receipt = self.execute()
        self.assertEqual(receipt["attempt"], str(self.attempt))
        self.assertEqual(receipt["candidate_manifest"], self.candidate_ref)
        self.assertEqual(receipt["preparation"], self.preparation_ref)
        self.assertEqual(receipt["verified"], self.expected_verified())
        self.assertEqual(matrix.queue.read_json(self.directory / "receipt.json"), receipt)
        self.assertGreaterEqual(source_check.call_count, 2)
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_interrupted_final_is_preserved_and_only_missing_final_stage_is_prepared(self):
        blocked_ref = self.json_ref(self.final / "preparation.json", {"status": "blocked"})
        before = Path(blocked_ref["path"]).read_bytes()
        raw_before, xz_before = self.raw_path.read_bytes(), self.xz_path.read_bytes()
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))

        def prepare(source, sha256, **kwargs):
            destination = kwargs["output"]
            self.assertEqual(source, str(self.xz_path))
            self.assertEqual(sha256, self.candidate["candidate"]["sha256"])
            self.assertEqual(destination, self.attempt / "final-0002")
            self.extraction(destination / "extraction", self.new, self.xz_path, "xz",
                            matrix.file_digest(self.raw_path))
            self.json_ref(destination / "family-result.json", matrix.queue.read_json(self.final / "family-result.json"))
            self.json_ref(destination / "preparation.json", self.preparation)

        prepare_mock = self.patch(matrix.preparation, "prepare", side_effect=prepare)
        receipt = self.execute()
        self.assertEqual(Path(receipt["preparation"]["path"]), self.attempt / "final-0002/preparation.json")
        self.assertEqual(Path(blocked_ref["path"]).read_bytes(), before)
        self.assertEqual((self.raw_path.read_bytes(), self.xz_path.read_bytes()), (raw_before, xz_before))
        prepare_mock.assert_called_once_with(
            str(self.xz_path), self.candidate["candidate"]["sha256"], family="mediatek", board="bpi-r2",
            kernel_release=repack.RELEASE, output=self.attempt / "final-0002", layout="disk",
            max_raw_bytes=self.row["contract"]["raw_bytes"], timeout=1800)
        create.assert_not_called()
        self.assertEqual(r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory), receipt)

    def test_missing_final_is_built_without_rebuilding_verified_candidate(self):
        saved = self.attempt / "saved-final"
        self.final.rename(saved)
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))

        def prepare(*args, **kwargs):
            self.assertEqual(kwargs["output"], self.final)
            saved.rename(self.final)

        prepare_mock = self.patch(matrix.preparation, "prepare", side_effect=prepare)
        receipt = self.execute()
        self.assertEqual(receipt["preparation"], self.preparation_ref)
        prepare_mock.assert_called_once()
        create.assert_not_called()

    def test_multiple_incomplete_finals_are_preserved_and_next_number_is_used(self):
        saved = self.attempt / "saved-final"
        self.final.rename(saved)
        self.final.mkdir()
        (self.final / "partial.bin").write_bytes(b"\x01")
        second = self.attempt / "final-0002"
        second.mkdir()
        (second / "partial.bin").write_bytes(b"\x02")
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))

        def prepare(*args, **kwargs):
            self.assertEqual(kwargs["output"], self.attempt / "final-0003")
            saved.rename(kwargs["output"])

        prepare_mock = self.patch(matrix.preparation, "prepare", side_effect=prepare)
        receipt = self.execute()
        self.assertEqual(Path(receipt["preparation"]["path"]), self.attempt / "final-0003/preparation.json")
        self.assertEqual((self.final / "partial.bin").read_bytes(), b"\x01")
        self.assertEqual((second / "partial.bin").read_bytes(), b"\x02")
        prepare_mock.assert_called_once()
        create.assert_not_called()
        self.assertEqual(r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory), receipt)

    def test_numbered_prepared_final_is_reused_without_another_preparation(self):
        new_final = self.attempt / "final-0002"
        self.final.rename(new_final)
        self.final.mkdir()
        self.json_ref(self.final / "preparation.json", {"status": "blocked"})
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))
        receipt = self.execute()
        self.assertEqual(Path(receipt["preparation"]["path"]), new_final / "preparation.json")
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_new_blocked_final_never_publishes_completed_receipt(self):
        saved = self.attempt / "saved-final"
        self.final.rename(saved)
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("完整候選不得重建"))

        def prepare(*args, **kwargs):
            saved.rename(kwargs["output"])
            self.json_ref(self.final / "preparation.json", {**self.preparation, "status": "blocked"})

        self.patch(matrix.preparation, "prepare", side_effect=prepare)
        with self.assertRaisesRegex(ValueError, "固定來源與根身分"):
            self.execute()
        self.assertFalse((self.directory / "receipt.json").exists())
        create.assert_not_called()

    def test_multiple_existing_candidates_are_rejected_without_rebuild_or_publication(self):
        self.json_ref(self.directory / "attempt-0002/candidate/candidate.json", self.candidate)
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("歧義候選不得重建"))
        with self.assertRaisesRegex(ValueError, "多個完整候選"):
            self.execute()
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()
        self.assertFalse((self.directory / "receipt.json").exists())

    def test_existing_wrong_candidate_or_preparation_never_publishes_or_rebuilds(self):
        original_candidate, original_preparation = copy.deepcopy(self.candidate), copy.deepcopy(self.preparation)
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("錯配不得改成重建"))
        for target, fields, value in (
                ("candidate", ("source", "sha256"), "0" * 64),
                ("candidate", ("raw", "original_sha256"), "0" * 64),
                ("candidate", ("changed_range", "before"), matrix.image.digest(b"0")),
                ("preparation", ("source", "sha256"), "0" * 64),
                ("preparation", ("root_uuid",), "unknown")):
            self.candidate, self.preparation = copy.deepcopy(original_candidate), copy.deepcopy(original_preparation)
            change_field(getattr(self, target), fields, value)
            self.refresh_refs()
            with self.subTest(target=target, fields=fields), self.assertRaises(ValueError):
                self.execute()
            self.assertFalse((self.directory / "receipt.json").exists())
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_existing_invalid_final_directory_cannot_publish_success_receipt(self):
        invalid = self.attempt / "final-extra"
        self.final.rename(invalid)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.directory / "receipt.json").exists())

    def test_tampered_completed_receipt_is_not_rebuilt_or_overwritten(self):
        receipt = self.receipt()
        receipt["row_sha256"] = "0" * 64
        path = self.directory / "receipt.json"
        self.json_ref(path, receipt)
        before = path.read_bytes()
        create = self.patch(repack, "create_candidate", side_effect=AssertionError("錯配收據不得重建"))
        with self.assertRaisesRegex(ValueError, "收據錯配"):
            self.execute()
        self.assertEqual(path.read_bytes(), before)
        create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()


class ExecutionTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.output = self.root / "batch"
        self.plan = {"source_root": str(self.root / "sources")}
        self.plan_ref = self.json_ref(self.output / "plan.json", self.plan)
        self.guard = self.patch(r2.review, "source_guard")
        self.create = self.patch(repack, "create_candidate", side_effect=self.create_candidate)
        self.prepare = self.patch(matrix.preparation, "prepare", side_effect=self.prepare_candidate)
        self.verified = {"root_uuid": "12345678-1234-1234-1234-123456789abc"}
        self.verify = self.patch(r2, "verify_candidate", return_value=self.verified)
        self.patch(sys, "stdout", io.StringIO())

    def create_candidate(self, source, output, *, contract):
        candidate = {"candidate": {"path": str(output / (contract["name"] + ".xz")), "sha256": "a" * 64}}
        self.json_ref(output / "candidate.json", candidate)
        return candidate

    def prepare_candidate(self, source, sha256, **kwargs):
        self.json_ref(kwargs["output"] / "preparation.json", {"status": "prepared"})

    def execute(self, row):
        return r2.execute_row(self.plan_ref, self.plan, row, self.output)

    def test_only_original_bookworm_minimal_reuses_pinned_legacy_candidate(self):
        results = []
        for release in RELEASES:
            for variant in VARIANTS:
                row = make_row(release, variant)
                legacy = (release, variant) == ("bookworm", "minimal")
                if legacy:
                    row["entry"]["expected_sha256"] = row["contract"]["source_sha256"] = repack.SOURCE_SHA256
                with self.subTest(release=release, variant=variant):
                    receipt = self.execute(row)
                    self.assertIs(receipt["reused_legacy"], legacy)
                    self.assertEqual(receipt["row_sha256"], matrix.queue.digest(row))
                    self.assertEqual(receipt["verified"], self.verified)
                    if legacy:
                        self.assertEqual(receipt["candidate_manifest"], r2.LEGACY_CANDIDATE)
                        self.assertEqual(receipt["preparation"], r2.LEGACY_PREPARATION)
                        self.assertIsNone(receipt["attempt"])
                        self.assertIsNone(receipt["artifacts"])
                    else:
                        self.assertEqual(Path(receipt["attempt"]).parent,
                                         self.output / "jobs" / row["entry"]["image_id"])
                        self.assertEqual(receipt["artifacts"], matrix.artifact_digest(Path(receipt["attempt"])))
                    results.append(receipt)
        self.assertEqual(sum(result["reused_legacy"] for result in results), 1)
        self.assertEqual(self.create.call_count, 9)
        self.assertEqual(self.prepare.call_count, 9)
        self.assertEqual(self.verify.call_count, 10)

    def test_same_bookworm_label_with_different_source_must_build_own_candidate(self):
        row = make_row("bookworm", "minimal")
        receipt = self.execute(row)
        self.assertFalse(receipt["reused_legacy"])
        self.create.assert_called_once_with(Path(self.plan["source_root"]) / row["entry"]["relative_path"],
                                            Path(receipt["attempt"]) / "candidate", contract=row["contract"])
        self.prepare.assert_called_once_with(
            str(Path(receipt["attempt"]) / "candidate" / (row["contract"]["name"] + ".xz")), "a" * 64,
            family="mediatek", board="bpi-r2", kernel_release=repack.RELEASE,
            output=Path(receipt["attempt"]) / "final", layout="disk",
            max_raw_bytes=row["contract"]["raw_bytes"], timeout=1800)

    def test_legacy_receipt_cannot_be_substituted_for_other_row_or_reference(self):
        row = make_row("bookworm", "minimal")
        row["entry"]["expected_sha256"] = row["contract"]["source_sha256"] = repack.SOURCE_SHA256
        receipt = self.execute(row)
        directory = self.output / "jobs" / row["entry"]["image_id"]
        self.assertEqual(r2.verify_receipt(receipt, self.plan_ref, self.plan, row, directory), receipt)
        for release, variant in ((release, variant) for release in RELEASES for variant in VARIANTS):
            other = make_row(release, variant)
            wrong = {**receipt, "row_sha256": matrix.queue.digest(other),
                     "image_id": other["entry"]["image_id"], "release": release, "variant": variant,
                     "source_sha256": other["entry"]["expected_sha256"]}
            with self.subTest(release=release, variant=variant), self.assertRaises(ValueError):
                r2.verify_receipt(wrong, self.plan_ref, self.plan, other, directory)
        for name in ("candidate_manifest", "preparation"):
            wrong = copy.deepcopy(receipt)
            wrong[name]["sha256"] = "0" * 64
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "重用範圍"):
                r2.verify_receipt(wrong, self.plan_ref, self.plan, row, directory)

    def test_failure_at_each_boundary_never_publishes_completion_receipt(self):
        for number, boundary in enumerate((self.guard, self.create, self.prepare, self.verify)):
            row = make_row(RELEASES[number])
            previous = boundary.side_effect
            boundary.side_effect = ValueError("測試階段失敗")
            with self.subTest(stage=number), self.assertRaisesRegex(ValueError, "測試階段失敗"):
                self.execute(row)
            directory = self.output / "jobs" / row["entry"]["image_id"]
            self.assertFalse((directory / "receipt.json").exists())
            self.assertFalse(list(directory.glob(".publish-*")))
            boundary.side_effect = previous


class PreviousBatchTests(CandidateFixture):
    def setUp(self):
        super().setUp()
        self.rows = [self.row if (release, variant) == ("noble", "minimal") else make_row(release, variant)
                     for release in RELEASES for variant in VARIANTS]
        self.rows[0]["entry"]["expected_sha256"] = self.rows[0]["contract"]["source_sha256"] = repack.SOURCE_SHA256
        self.patch(matrix, "OUTPUT_ROOT", self.root / "evidence")
        self.patch(r2, "source_rows", return_value=(str(self.source_root), self.rows))
        self.new_tools = [{"path": str(self.build_tool), **matrix.file_digest(self.build_tool)}]
        self.patch(matrix, "tool_binding", return_value=self.new_tools)
        self.plan.update(schema="bpi-r2-matrix-plan-v1", audit=r2.AUDIT, rows=self.rows, workers=2,
                         internal_only=True, hardware_validated=False, boot_blob_redistribution_authorized=False,
                         tools=[{"path": str(self.build_tool), "bytes": 1, "sha256": "0" * 64}], reuses={})
        self.plan_ref = self.json_ref(self.output / "plan.json", self.plan)
        self.old_receipt = self.receipt()
        self.old_ref = self.json_ref(self.directory / "receipt.json", self.old_receipt)
        self.new_output = self.root / "evidence/next-batch"
        self.create = self.patch(repack, "create_candidate", side_effect=AssertionError("跨批重用不得重建"))
        for name in ("copyfile", "copy2", "copytree"):
            self.patch(r2.shutil, name, side_effect=AssertionError("跨批重用不得複製候選"))

    def initialize_next(self):
        ref = r2.initialize(self.new_output, previous_plan_ref=self.plan_ref)
        return ref, r2.review.ref_data(ref)

    def execute_next(self, ref, plan):
        return r2.execute_row(ref, plan, self.row, self.new_output)

    def test_previous_receipt_without_new_reuse_fields_is_fully_reverified(self):
        self.assertNotIn("reused_previous", self.old_receipt)
        self.assertNotIn("origin_receipt", self.old_receipt)
        with mock.patch.object(r2, "verify_receipt", wraps=r2.verify_receipt) as verify:
            self.assertEqual(r2.verify_previous(self.old_ref, self.row, str(self.source_root)), self.old_receipt)
        verify.assert_called_once_with(self.old_receipt, self.plan_ref, self.plan, self.row, self.directory, depth=1)
        self.create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_initialization_only_indexes_completed_nonlegacy_receipts(self):
        legacy = self.rows[0]
        legacy_path = self.output / "jobs" / legacy["entry"]["image_id"] / "receipt.json"
        self.json_ref(legacy_path, {"reused_legacy": True, "status": "prepared"})
        unfinished = self.output / "jobs" / self.rows[1]["entry"]["image_id"] / "attempt-0001"
        self.json_ref(unfinished / "candidate/candidate.json", {"status": "prepared"})
        ref, plan = self.initialize_next()
        self.assertEqual(plan["reuses"], {self.row["entry"]["image_id"]: self.old_ref})
        self.assertEqual(plan["tools"], self.new_tools)
        self.assertNotEqual(plan["tools"], self.plan["tools"])
        self.assertEqual(plan["rows"], self.rows)
        self.assertEqual(Path(ref["path"]), self.new_output / "plan.json")
        self.assertFalse(list((self.new_output / "jobs").rglob("candidate.json")))
        self.create.assert_not_called()

    def test_bad_previous_plan_or_incomplete_receipt_never_publishes_new_plan(self):
        with self.assertRaises(ValueError):
            r2.initialize(self.new_output, previous_plan_ref={**self.plan_ref, "sha256": "0" * 64})
        self.assertFalse((self.new_output / "plan.json").exists())
        self.json_ref(self.directory / "receipt.json", {**self.old_receipt, "status": "blocked"})
        with self.assertRaises(ValueError):
            self.initialize_next()
        self.assertFalse((self.new_output / "plan.json").exists())
        self.create.assert_not_called()

    def test_previous_batch_cannot_initialize_over_itself(self):
        before = (self.output / "plan.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "相同批次"):
            r2.initialize(self.output, previous_plan_ref=self.plan_ref)
        self.assertEqual((self.output / "plan.json").read_bytes(), before)
        self.assertEqual(r2.review.ref_data(self.old_ref), self.old_receipt)

    def test_cross_batch_execution_and_resume_never_copy_or_rebuild_candidate(self):
        before = matrix.artifact_digest(self.attempt)
        ref, plan = self.initialize_next()
        receipt = self.execute_next(ref, plan)
        self.assertIs(receipt["reused_previous"], True)
        self.assertIs(receipt["reused_legacy"], False)
        self.assertEqual(receipt["origin_receipt"], self.old_ref)
        self.assertEqual(receipt["candidate_manifest"], self.candidate_ref)
        self.assertEqual(receipt["preparation"], self.preparation_ref)
        self.assertEqual(receipt["verified"], self.expected_verified())
        self.assertIsNone(receipt["attempt"])
        self.assertIsNone(receipt["artifacts"])
        directory = self.new_output / "jobs" / self.row["entry"]["image_id"]
        self.assertEqual(matrix.queue.read_json(directory / "receipt.json"), receipt)
        self.assertFalse(list(directory.glob("attempt-*")))
        with mock.patch.object(matrix, "publish_json", side_effect=AssertionError("續作不得重發")):
            self.assertEqual(self.execute_next(ref, plan), receipt)
        self.assertEqual(matrix.artifact_digest(self.attempt), before)
        self.assertEqual(r2.review.ref_data(self.old_ref), self.old_receipt)
        self.create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_previous_plan_fixed_rows_root_audit_and_restrictions_cannot_change(self):
        original = copy.deepcopy(self.plan)
        cases = [(('schema',), "unknown"), (('audit', 'sha256'), "0" * 64),
                 (('rows',), self.rows[:-1]), (('source_root',), str(self.root / "other")),
                 (('workers',), 4), (('internal_only',), False), (('hardware_validated',), True),
                 (('boot_blob_redistribution_authorized',), True)]
        for fields, value in cases:
            plan = copy.deepcopy(original)
            change_field(plan, fields, value)
            plan_ref = self.json_ref(self.output / "plan.json", plan)
            receipt = {**self.old_receipt, "plan": plan_ref}
            old_ref = self.json_ref(self.directory / "receipt.json", receipt)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                r2.verify_previous(old_ref, self.row, str(self.source_root))

    def test_previous_receipt_row_and_directory_must_match_exactly(self):
        with self.assertRaisesRegex(ValueError, "原來源不符"):
            r2.verify_previous(self.old_ref, self.row, str(self.root / "other-sources"))
        for other in (self.rows[1], {**self.row, "root_uuid": "unknown"}):
            with self.subTest(row=other["entry"]["image_id"]), self.assertRaises(ValueError):
                r2.verify_previous(self.old_ref, other, str(self.source_root))
        for path in (self.output / "jobs" / self.rows[1]["entry"]["image_id"] / "receipt.json",
                     self.directory / "other.json", self.root / "outside/receipt.json"):
            ref = self.json_ref(path, self.old_receipt)
            with self.subTest(path=path), self.assertRaises(ValueError):
                r2.verify_previous(ref, self.row, str(self.source_root))

    def test_previous_receipt_incomplete_legacy_or_wrong_identity_is_rejected(self):
        for field, value in (("status", "blocked"), ("reused_legacy", True), ("image_id", "0" * 64),
                             ("release", "jammy"), ("variant", "xfce_desktop"),
                             ("source_sha256", "0" * 64), ("row_sha256", "0" * 64)):
            receipt = {**self.old_receipt, field: value}
            ref = self.json_ref(self.directory / "receipt.json", receipt)
            with self.subTest(field=field), self.assertRaises(ValueError):
                r2.verify_previous(ref, self.row, str(self.source_root))

    def test_old_receipt_cannot_smuggle_origin_without_explicit_reuse(self):
        for reused in (None, False):
            receipt = {**self.old_receipt, "origin_receipt": self.old_ref}
            if reused is not None:
                receipt["reused_previous"] = reused
            with self.subTest(reused=reused), self.assertRaises(ValueError):
                r2.verify_receipt(receipt, self.plan_ref, self.plan, self.row, self.directory)

    def test_declared_previous_reuse_cannot_be_silently_ignored(self):
        ref, plan = self.initialize_next()
        receipt = {**self.old_receipt, "plan": ref}
        with self.assertRaisesRegex(ValueError, "未使用計畫固定"):
            r2.verify_receipt(receipt, ref, plan, self.row, self.directory)

    def test_reused_receipt_origin_must_exactly_match_new_plan_reference(self):
        ref, plan = self.initialize_next()
        receipt = self.execute_next(ref, plan)
        directory = self.new_output / "jobs" / self.row["entry"]["image_id"]
        cases = [(('origin_receipt', 'sha256'), "0" * 64),
                 (('origin_receipt', 'path'), str(self.directory / "other.json")),
                 (('attempt',), str(self.attempt)), (('artifacts',), self.old_receipt["artifacts"]),
                 (('reused_legacy',), True), (('reused_previous',), 1),
                 (('candidate_manifest', 'sha256'), "0" * 64), (('preparation', 'sha256'), "0" * 64),
                 (('verified', 'root_uuid'), "unknown")]
        for fields, value in cases:
            wrong = copy.deepcopy(receipt)
            change_field(wrong, fields, value)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                r2.verify_receipt(wrong, ref, plan, self.row, directory)
        for reuses in ({}, {self.row["entry"]["image_id"]: {**self.old_ref, "sha256": "0" * 64}}):
            wrong_plan = {**plan, "reuses": reuses}
            with self.subTest(reuses=reuses), self.assertRaises(ValueError):
                r2.verify_receipt(receipt, ref, wrong_plan, self.row, directory)

    def test_tampered_previous_receipt_or_artifact_never_publishes_or_rebuilds(self):
        ref, plan = self.initialize_next()
        for path in (self.directory / "receipt.json", self.raw_path, self.xz_path,
                     self.final / "extraction/environment.bin"):
            before = path.read_bytes()
            path.write_bytes(before + b"\n")
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.execute_next(ref, plan)
            self.assertFalse((self.new_output / "jobs" / self.row["entry"]["image_id"] / "receipt.json").exists())
            path.write_bytes(before)
        self.create.assert_not_called()
        matrix.preparation.prepare.assert_not_called()

    def test_previous_chain_depth_is_bounded(self):
        with self.assertRaises(ValueError):
            r2.verify_previous(self.old_ref, self.row, str(self.source_root), depth=9)
        origin = self.old_ref
        for number in range(1, 10):
            output = self.root / "evidence" / f"chain-{number}"
            plan = {**self.plan, "reuses": {self.row["entry"]["image_id"]: origin}}
            ref = self.json_ref(output / "plan.json", plan)
            receipt = {**self.old_receipt, "plan": ref, "reused_previous": True,
                       "origin_receipt": origin, "attempt": None, "artifacts": None}
            origin = self.json_ref(output / "jobs" / self.row["entry"]["image_id"] / "receipt.json", receipt)
            # 原始收據加七層重用恰為八層；再多一層必須拒絕。
            if number in (1, 7):
                self.assertEqual(r2.verify_previous(origin, self.row, str(self.source_root)), receipt)
            elif number == 8:
                with self.assertRaisesRegex(ValueError, "重用鏈過深"):
                    r2.verify_previous(origin, self.row, str(self.source_root))
        with self.assertRaises(ValueError):
            r2.verify_previous(origin, self.row, str(self.source_root))


class PlanTests(TemporaryTests):
    def setUp(self):
        super().setUp()
        self.rows = [make_row(release, variant) for release in RELEASES for variant in VARIANTS]
        self.rows[0]["entry"]["expected_sha256"] = repack.SOURCE_SHA256
        self.rows[0]["contract"]["source_sha256"] = repack.SOURCE_SHA256
        self.source_root = str(self.root / "sources")
        self.output_root = self.root / "evidence"
        self.patch(matrix, "OUTPUT_ROOT", self.output_root)
        self.patch(r2, "source_rows", return_value=(self.source_root, self.rows))
        self.tools = [{"path": str(self.root / "tool.py"), "bytes": 8, "sha256": "a" * 64}]
        self.binding = self.patch(matrix, "tool_binding", return_value=self.tools)
        self.patch(r2.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * 1024**3))
        self.output = self.output_root / "batch"
        self.plan_ref = r2.initialize(self.output)
        self.plan = r2.review.ref_data(self.plan_ref)
        self.execute = self.patch(r2, "execute_row", side_effect=self.complete_row)
        self.patch(sys, "stdout", io.StringIO())

    def complete_row(self, plan_ref, plan, row, output):
        path = output / "jobs" / row["entry"]["image_id"] / "receipt.json"
        if path.exists():
            return matrix.queue.read_json(path)
        receipt = {"image_id": row["entry"]["image_id"], "status": "prepared",
                   "reused_legacy": row["entry"]["expected_sha256"] == repack.SOURCE_SHA256}
        self.json_ref(path, receipt)
        return receipt

    def test_initialization_pins_sources_tools_and_internal_only_flags(self):
        self.assertEqual(self.plan["rows"], self.rows)
        self.assertEqual(self.plan["tools"], self.tools)
        self.assertEqual(self.plan["audit"], r2.AUDIT)
        self.assertEqual(self.plan["workers"], 2)
        self.assertIs(self.plan["internal_only"], True)
        self.assertIs(self.plan["hardware_validated"], False)
        self.assertIs(self.plan["boot_blob_redistribution_authorized"], False)
        self.assertTrue((self.output / "jobs").is_dir())

    def test_reuse_mapping_accepts_absent_old_field_but_rejects_wrong_type_or_scope(self):
        old_plan = {key: value for key, value in self.plan.items() if key != "reuses"}
        r2.validate_plan(old_plan, self.source_root, self.rows)
        r2.validate_plan({**self.plan, "reuses": {}}, self.source_root, self.rows)
        for reuses in (None, [], True, {"not-a-fixed-image": self.plan_ref}):
            with self.subTest(reuses=reuses), self.assertRaisesRegex(ValueError, "重用範圍"):
                r2.validate_plan({**self.plan, "reuses": reuses}, self.source_root, self.rows)

    def test_plan_directory_requires_exact_filename_scoped_path_and_no_symlink(self):
        self.assertEqual(r2.plan_directory(self.plan_ref), self.output)
        for path in (self.output / "other.json", self.output_root / "plan.json", self.root / "outside/plan.json",
                     self.output_root / "batch/../escape/plan.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "目錄越界"):
                r2.plan_directory({**self.plan_ref, "path": str(path)})
        link = self.output_root / "linked-batch"
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            r2.plan_directory({**self.plan_ref, "path": str(link / "plan.json")})

    def test_initialization_rejects_existing_root_outside_and_parent_traversal(self):
        for path in (self.output_root, self.root / "outside", self.output_root / "child/../escape", self.output):
            before = (self.output / "plan.json").read_bytes()
            with self.subTest(path=path), self.assertRaises((ValueError, FileExistsError)):
                r2.initialize(path)
            self.assertEqual((self.output / "plan.json").read_bytes(), before)

    def test_plan_identity_source_tools_and_safety_flags_cannot_change(self):
        cases = [(('schema',), "unknown"), (('audit', 'sha256'), "0" * 64),
                 (('source_root',), str(self.root / "other")), (('workers',), 4),
                 (('internal_only',), False), (('hardware_validated',), True),
                 (('boot_blob_redistribution_authorized',), True), (('rows',), self.rows[:-1]),
                 (('tools',), [])]
        for fields, value in cases:
            plan = copy.deepcopy(self.plan)
            change_field(plan, fields, value)
            ref = self.json_ref(self.output / "plan.json", plan)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                r2.run(ref)
            self.assertFalse((self.output / "summary.json").exists())
        self.execute.assert_not_called()

    def test_tool_drift_before_or_during_run_never_publishes_summary(self):
        for during in (False, True):
            self.execute.reset_mock()
            self.binding.side_effect = [self.tools, []] if during else [[]]
            with self.subTest(during=during), self.assertRaisesRegex(ValueError, "工具"):
                r2.run(self.plan_ref)
            self.assertEqual(self.execute.call_count, 2 if during else 0)
            self.assertFalse((self.output / "summary.json").exists())

    def test_low_space_or_row_failure_never_publishes_summary(self):
        with mock.patch.object(r2.shutil, "disk_usage", return_value=SimpleNamespace(free=80 * 1024**3 - 1)):
            with self.assertRaisesRegex(ValueError, "剩餘空間"):
                r2.run(self.plan_ref)
        self.execute.assert_not_called()
        self.execute.side_effect = ValueError("測試單列失敗")
        with self.assertRaisesRegex(ValueError, "測試單列失敗"):
            r2.run(self.plan_ref)
        self.assertFalse((self.output / "summary.json").exists())

    def test_success_counts_ten_rows_and_summary_resume_does_not_republish(self):
        first = r2.run(self.plan_ref)
        self.assertEqual((first["checked"], first["prepared"], first["reused_legacy"]), (10, 10, 1))
        self.assertEqual(len(first["results"]), 10)
        self.assertEqual(self.execute.call_count, 10)
        path = self.output / "summary.json"
        before, identity = path.read_bytes(), path.stat()
        with mock.patch.object(matrix, "publish_json", side_effect=AssertionError("既有總結不得重發")):
            self.assertEqual(r2.run(self.plan_ref), first)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), (identity.st_ino, identity.st_mtime_ns))

    def test_tampered_summary_is_rejected_without_overwrite(self):
        result = r2.run(self.plan_ref)
        result["prepared"] = 9
        path = self.output / "summary.json"
        self.json_ref(path, result)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "續作總結"):
            r2.run(self.plan_ref)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
