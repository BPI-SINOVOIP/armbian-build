"""離線矩陣的固定來源、收據、重播與續跑回歸；合成資料不是實板證據。"""

import contextlib
import copy
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_lab_matrix as matrix
import bpi_lab_amlogic as amlogic
import bpi_lab_special as special


def source_entry(root, board="bpi-f2p", release="bookworm", variant="minimal", *,
                 architecture="arm32", kernel="0", branch="legacy", family="sunplus-sp7021-bpi"):
    profile = matrix.catalog_api.BOARD_PROFILES[board]
    name = f"Armbian-unofficial_26.11.0-trunk_{profile.capitalize()}_{release}_{branch}_{kernel}_{variant}.img.xz"
    path = root / board / name
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = ("合成來源：" + board + release + variant).encode()
    path.write_bytes(blob)
    path.with_name(name + ".sha").write_text(hashlib.sha256(blob).hexdigest() + "  " + name + "\n")
    with matrix.catalog_api._root(root) as fd:
        entry = matrix.catalog_api._entry(fd, board + "/" + name, path.stat())
    entry.update(architecture=architecture, family=family)
    return entry


def snapshot(directory, root, entry, *, origin=None, files=None, missing=("/absent",), changes=None):
    directory.mkdir(parents=True)
    index, queries = {}, []
    for number, (path, blob) in enumerate((files or {"/boot/synthetic.bin": b"\x01\x02\x03"}).items()):
        name = f"file-{number:04d}.bin"
        (directory / name).write_bytes(blob)
        index[path] = {"resolved": path, "links": [], "file": name, "digest": matrix.image.digest(blob)}
    for path in missing:
        name = f"query-{len(queries):04d}.stderr"
        blob = (path + ": File not found by ext2_lookup\n").encode()
        (directory / name).write_bytes(blob)
        queries.append({"command": "stat " + path, "returncode": 0, "lookup_path": path,
                        "stderr_file": name, "stderr": matrix.image.digest(blob)})
    data = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
            "source_kind": "xz", "hardware_validated": False, "mounted": False,
            "image_code_executed": False, "source": str(root / entry["relative_path"]),
            "source_digest": {"bytes": entry["compressed_bytes"], "sha256": entry["expected_sha256"]},
            "filesystem_uuid": "12345678-1234-1234-1234-123456789abc",
            "raw": {"bytes": 8 * 1024**2, "sha256": "a" * 64},
            "partition": {"index": 1, "start_lba": 2048, "sectors": 4096},
            "files": index, "queries": queries}
    if origin:
        data.update(schema="bpi-lab-image-replay-v1", source_reread=False, replay_of=origin)
    data.update(changes or {})
    return matrix.queue.save_json(directory / "extraction.json", data)


class MatrixRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        entries = [source_entry(root, board, release, variant)
                   for board, release, variant in sorted(matrix.review.MATRIX)]
        boards = sorted(set(matrix.catalog_api.BOARD_PROFILES) - set(matrix.review.BOARDS))
        for number in range(424):
            board_index, combination = number % 43, number // 43
            entries.append(source_entry(
                root, boards[board_index], matrix.review.RELEASES[combination // 2],
                matrix.review.VARIANTS[combination % 2],
                architecture=matrix.catalog_api.ARCHITECTURES[board_index % 3],
                kernel="6.6.1", branch="current", family="synthetic-family"))
        cls.original = {"schema": "bpi-lab-catalog-v1", "root": str(root), "entries": entries,
                        "hardware_validated": False, "metadata_only": True,
                        "issues": [issue for entry in entries for issue in entry["issues"]],
                        "boards": [{"board": board, "issues": [issue for entry in entries
                                    if entry["board"] == board for issue in entry["issues"]]}
                                   for board in sorted({entry["board"] for entry in entries})]}
        samples = []
        for board in cls.original["boards"]:
            entry = next(e for e in entries if e["board"] == board["board"])
            samples.append({"board": entry["board"], "architecture": entry["architecture"],
                            "family": "sunplus" if entry["kernel"] == "0" else "allwinner",
                            "kernel_release": "5.4.35-legacy-sunplus-sp7021-bpi" if entry["kernel"] == "0"
                                              else "6.6.1-current-synthetic",
                            "source": {"relative_path": entry["relative_path"],
                                       "sha256": entry["expected_sha256"], "bytes": entry["compressed_bytes"]}})
        cls.samples = {"schema": "bpi-lab-readonly-sample-audit-v1", "rows": samples}

    def setUp(self):
        self.catalog, self.sample = copy.deepcopy(self.original), copy.deepcopy(self.samples)

    def test_all_444_identities_preserved_in_total_order(self):
        self.catalog["entries"].reverse()
        self.sample["rows"].reverse()
        before = copy.deepcopy((self.catalog, self.sample))
        rows = matrix.matrix_rows(self.catalog, self.sample)
        def key(entry):
            return (matrix.catalog_api.ARCHITECTURES.index(entry["architecture"]), entry["release"],
                    entry["board"], entry["variant"], entry["relative_path"])
        self.assertEqual([r["entry"] for r in rows], sorted(self.original["entries"], key=key))
        self.assertEqual(len({r["entry"]["image_id"] for r in rows}), 444)
        self.assertEqual(len({r["entry"]["board"] for r in rows}), 45)
        self.assertEqual((self.catalog, self.sample), before)

    def test_unknown_kernel_keeps_binary_discovery_request(self):
        rows = matrix.matrix_rows(self.catalog, self.sample)
        unknown = [r for r in rows if r["entry"]["kernel"] == "0"]
        self.assertEqual(len(unknown), 20)
        self.assertTrue(all(r["kernel_release"] == "0" for r in unknown))
        self.assertTrue(all("status" not in r and "hardware_validated" not in r for r in rows))

    def test_catalog_subset_and_duplicate_identity_rejected(self):
        for mode in ("subset", "duplicate"):
            catalog = copy.deepcopy(self.catalog)
            if mode == "subset":
                catalog["entries"].pop()
            else:
                catalog["entries"][1]["image_id"] = catalog["entries"][0]["image_id"]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                matrix.matrix_rows(catalog, self.sample)

    def test_sample_missing_duplicate_and_unknown_board_rejected(self):
        for mode in ("missing", "duplicate", "unknown"):
            sample = copy.deepcopy(self.sample)
            if mode == "missing":
                sample["rows"].pop()
            elif mode == "duplicate":
                sample["rows"][-1] = copy.deepcopy(sample["rows"][0])
            else:
                sample["rows"][0]["board"] = "bpi-unknown"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                matrix.matrix_rows(self.catalog, sample)

    def test_sample_source_identity_mismatch_rejected(self):
        cases = (("relative_path", "bpi-f2p/not-in-catalog.img.xz"), ("sha256", "0" * 64), ("bytes", 1))
        for field, value in cases:
            sample = copy.deepcopy(self.sample)
            sample["rows"][0]["source"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                matrix.matrix_rows(self.catalog, sample)
        sample = copy.deepcopy(self.sample)
        sample["rows"][0]["source"] = copy.deepcopy(sample["rows"][1]["source"])
        with self.assertRaises(ValueError):
            matrix.matrix_rows(self.catalog, sample)

    def test_sample_architecture_family_and_kernel_mismatch_rejected(self):
        for field, value in (("architecture", "wrong"), ("family", "unsupported"), ("kernel_release", "6.6.10-current")):
            sample = copy.deepcopy(self.sample)
            target = next(r for r in sample["rows"] if r["family"] != "sunplus")
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                matrix.matrix_rows(self.catalog, sample)

    def test_other_os_cannot_inherit_different_kernel_branch_or_family(self):
        baseline = next(r for r in self.sample["rows"] if r["family"] != "sunplus")
        for field, value in (("kernel", "6.6.2"), ("branch", "edge"), ("family", "different")):
            catalog = copy.deepcopy(self.catalog)
            target = next(e for e in catalog["entries"] if e["board"] == baseline["board"]
                          and e["relative_path"] != baseline["source"]["relative_path"])
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                matrix.matrix_rows(catalog, self.sample)


class MatrixExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "sources"
        self.entries = [source_entry(self.source, variant=variant) for variant in ("minimal", "xfce_desktop")]
        self.tools = [{"path": str(self.root / "synthetic-tool"), "bytes": 1, "sha256": "a" * 64}]
        self.result_changes, self.component_reads, self.extraction_changes = {}, [], {}
        self.component_sources = None
        self.serial = 0
        self.configure()
        for patcher in (mock.patch.object(matrix, "OUTPUT_ROOT", self.root),
                        mock.patch.object(matrix, "matrix_rows", side_effect=lambda *_: self.expected_rows),
                        mock.patch.object(matrix, "tool_binding", return_value=self.tools),
                        mock.patch.object(matrix.shutil, "disk_usage", return_value=SimpleNamespace(free=2**50))):
            patcher.start()
            self.addCleanup(patcher.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def configure(self, entries=None, *, replays=None):
        self.serial += 1
        self.output = self.root / f"batch-{self.serial}"
        (self.output / "jobs").mkdir(parents=True)
        entries = entries if entries is not None else self.entries
        rows = [{"entry": e, "family": "sunplus", "kernel_release": "0"} for e in entries]
        self.expected_rows = copy.deepcopy(rows)
        catalog = matrix.queue.save_json(self.output / "catalog.json", {"entries": entries, "root": str(self.source)})
        sample = matrix.queue.save_json(self.output / "sample.json", {})
        self.plan = {"schema": "bpi-lab-matrix-plan-v1", "catalog": catalog, "sample": sample,
                     "source_root": str(self.source), "rows": rows, "tools": self.tools,
                     "replays": replays or {}, "workers": 4, "max_raw_bytes": 1024**3, "timeout": 60,
                     "hardware_validated": False, "media_written": False}
        self.plan_ref = matrix.queue.save_json(self.output / "plan.json", self.plan)

    def prepare(self, source, sha256, *, output, from_extraction, **kwargs):
        """只替代解析工作，保留真實固定引用、來源守門及檔案摘要。"""
        matrix.image.create_directory(output)
        if from_extraction:
            original = matrix.review.read_fixed(source, sha256)
            source_path = original["source"]
        else:
            source_path = str(source)
        entry = next(r["entry"] for r in self.plan["rows"]
                     if str(self.source / r["entry"]["relative_path"]) == source_path)
        extraction = snapshot(output / "extraction", self.source, entry,
                              origin={"path": str(source), "sha256": sha256} if from_extraction else None,
                              changes=self.extraction_changes)
        status = self.result_changes.get("status", "prepared")
        component = {"hardware_validated": False, "reads": self.component_reads, "status": status,
                     "board": kwargs["board"], "blockers": self.result_changes.get("blockers", [])}
        if self.component_sources is not None:
            component["sources"] = self.component_sources
        component_ref = matrix.queue.save_json(output / "family-result.json", component)
        ext = matrix.review.ref_data(extraction)
        result = {"schema": "bpi-lab-prepare-v1", "status": "prepared", "blockers": [],
                  "board": kwargs["board"], "family": kwargs["family"], "component_status": status,
                  "kernel_release": "5.4.35-legacy-sunplus-sp7021-bpi", "hardware_validated": False,
                  "whole_backend_ready": False, "media_written": False, "boot_executed": False,
                  "layout": "disk", "source": ext["source_digest"], "raw": ext["raw"], "partition": ext["partition"],
                  "source_reread": not from_extraction,
                  "extraction": {"path": "extraction/extraction.json", **matrix.file_digest(extraction["path"])},
                  "components": {"path": "family-result.json", **matrix.file_digest(component_ref["path"])},
                  **self.result_changes}
        matrix.queue.save_json(output / "preparation.json", result)
        return result

    def execute(self, index=0):
        return matrix.execute_row(self.plan_ref, self.plan, self.plan["rows"][index], self.output)

    def directory(self, index=0):
        return self.output / "jobs" / self.plan["rows"][index]["entry"]["image_id"]

    def receipt(self):
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare):
            return self.execute()

    def verify(self, receipt):
        return matrix.verify_completed(receipt, self.plan_ref, self.plan, self.plan["rows"][0], self.directory())

    def replay(self, *, entry=None, changes=None):
        entry = entry or self.entries[0]
        ref = snapshot(self.root / "old-extraction", self.source, entry, changes=changes)
        self.configure(replays={self.entries[0]["image_id"]: ref})
        return ref

    def test_source_changed_before_prepare_rejected(self):
        path = self.source / self.entries[0]["relative_path"]
        path.write_bytes(b"\x00")
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            self.execute()
        prepare.assert_not_called()
        self.assertFalse(self.directory().exists())

    def test_source_replaced_with_symlink_rejected(self):
        path = self.source / self.entries[0]["relative_path"]
        path.unlink()
        path.symlink_to(self.source / self.entries[1]["relative_path"])
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            self.execute()
        prepare.assert_not_called()

    def test_sidecar_changed_rejects_completed_receipt(self):
        receipt = self.receipt()
        sidecar = self.source / self.entries[0]["sidecar"]["path"]
        sidecar.write_text("0" * 64 + "  " + Path(self.entries[0]["relative_path"]).name + "\n")
        with self.assertRaises(ValueError):
            self.verify(receipt)

    def test_source_changed_during_prepare_leaves_attempt_without_receipt(self):
        def changing(*args, **kwargs):
            result = self.prepare(*args, **kwargs)
            (self.source / self.entries[0]["relative_path"]).write_bytes(b"\x00")
            return result
        with mock.patch.object(matrix.preparation, "prepare", side_effect=changing), self.assertRaises(ValueError):
            self.execute()
        self.assertTrue((self.directory() / "attempt-0001/source/preparation.json").is_file())
        self.assertFalse((self.directory() / "receipt.json").exists())

    def test_completed_receipt_preserves_offline_flags(self):
        receipt = self.receipt()
        self.assertEqual(self.verify(receipt), receipt)
        self.assertFalse(receipt["hardware_validated"])
        self.assertFalse(receipt["media_written"])

    def test_completed_artifact_changed_deleted_added_or_linked_rejected(self):
        for mode in ("changed", "deleted", "added", "linked"):
            self.configure()
            receipt = self.receipt()
            attempt = Path(receipt["attempt"])
            path = attempt / "source/extraction/file-0000.bin"
            if mode == "changed":
                path.write_bytes(b"\x03\x02\x01")
            elif mode == "deleted":
                path.unlink()
            elif mode == "added":
                (attempt / "extra.bin").write_bytes(b"\x00")
            else:
                path.unlink()
                path.symlink_to(self.source / self.entries[0]["relative_path"])
            with self.subTest(mode=mode), self.assertRaises((ValueError, OSError)):
                self.verify(receipt)

    def test_receipt_binding_and_attempt_escape_rejected(self):
        receipt = self.receipt()
        cases = {"schema": "unknown", "plan": {**self.plan_ref, "sha256": "0" * 64},
                 "row_sha256": "0" * 64, "status": "running", "hardware_validated": True, "media_written": True,
                 "attempt": str(self.root / "attempt-0001")}
        for field, value in cases.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify({**receipt, field: value})

    def test_completed_empty_attempt_or_missing_preparation_rejected_even_if_rehashed(self):
        for empty in (False, True):
            self.configure()
            receipt = self.receipt()
            attempt = Path(receipt["attempt"])
            if empty:
                attempt = self.directory() / "attempt-9999"
                attempt.mkdir()
                receipt["attempt"] = str(attempt)
            else:
                (attempt / "source/preparation.json").unlink()
            receipt["artifacts"] = matrix.artifact_digest(attempt)
            with self.subTest(empty=empty), self.assertRaises((ValueError, OSError)):
                self.verify(receipt)

    def test_completed_attempt_symlink_rejected(self):
        receipt = self.receipt()
        attempt = Path(receipt["attempt"])
        moved = self.root / "moved-attempt"
        attempt.rename(moved)
        attempt.symlink_to(moved, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.verify(receipt)

    def test_completed_preparation_semantics_rejected_even_if_rehashed(self):
        cases = {"status": "blocked", "board": "bpi-f2s", "family": "amlogic",
                 "source": {"bytes": 1, "sha256": "0" * 64}, "hardware_validated": True,
                 "media_written": True, "boot_executed": True, "whole_backend_ready": True}
        for field, value in cases.items():
            self.configure()
            receipt = self.receipt()
            record = receipt["attempts"][-1]
            ref = record["preparation"]
            data = matrix.review.ref_data(ref)
            data[field] = value
            Path(ref["path"]).write_bytes(matrix.queue.encode(data))
            record["preparation"] = matrix.review.reference(ref["path"])
            receipt["artifacts"] = matrix.artifact_digest(Path(receipt["attempt"]))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(receipt)

    def test_completed_extraction_cross_source_rejected_even_if_rehashed(self):
        receipt = self.receipt()
        prep_path = Path(receipt["attempts"][-1]["preparation"]["path"])
        prep = matrix.review.ref_data(receipt["attempts"][-1]["preparation"])
        ext_path = prep_path.parent / prep["extraction"]["path"]
        ext = matrix.queue.read_json(ext_path)
        ext["source"] = str(self.source / self.entries[1]["relative_path"])
        ext_path.write_bytes(matrix.queue.encode(ext))
        prep["extraction"].update(matrix.file_digest(ext_path))
        prep_path.write_bytes(matrix.queue.encode(prep))
        receipt["attempts"][-1]["preparation"] = matrix.review.reference(prep_path)
        receipt["artifacts"] = matrix.artifact_digest(Path(receipt["attempt"]))
        with self.assertRaises(ValueError):
            self.verify(receipt)

    def test_build_source_dict_and_list_changes_reject_completed_receipt(self):
        source = self.root / "build-helper.py"
        for listed in (False, True):
            self.configure()
            source.write_bytes(b"VALUE = 1\n")
            metadata = {"source_path": source.name, **matrix.file_digest(source)}
            self.component_sources = [metadata] if listed else {source.name: metadata}
            with self.subTest(listed=listed), mock.patch.object(matrix, "ROOT", self.root):
                receipt = self.receipt()
                self.assertEqual(self.verify(receipt), receipt)
                source.write_bytes(b"VALUE = 2\n")
                with self.assertRaisesRegex(ValueError, "建置來源"):
                    self.verify(receipt)

    def test_build_source_changed_during_prepare_prevents_receipt_publication(self):
        source = self.root / "build-helper.py"
        source.write_bytes(b"VALUE = 1\n")
        self.component_sources = {source.name: matrix.file_digest(source)}

        def changing(*args, **kwargs):
            result = self.prepare(*args, **kwargs)
            source.write_bytes(b"VALUE = 2\n")
            return result

        with mock.patch.object(matrix, "ROOT", self.root), \
                mock.patch.object(matrix.preparation, "prepare", side_effect=changing), self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.directory() / "receipt.json").exists())

    def test_build_source_absolute_and_parent_paths_rejected(self):
        for path in (str(self.root / "helper.py"), "../helper.py"):
            for listed in (False, True):
                metadata = {"source_path": path, "sha256": "a" * 64}
                sources = [metadata] if listed else {path: metadata}
                with self.subTest(path=path, listed=listed), self.assertRaisesRegex(ValueError, "越界"):
                    matrix.verify_build_sources({"sources": sources})

    def test_run_resume_does_not_prepare_twice_or_overwrite(self):
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            first = matrix.run(self.plan_ref)
            original = matrix.artifact_digest(self.output)
            prepare.reset_mock()
            second = matrix.run(self.plan_ref)
        prepare.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first["checked"], 2)
        self.assertEqual(first["full_source_reads"], 2)
        self.assertEqual(matrix.artifact_digest(self.output), original)

    def test_confirmed_blocked_is_resumed_without_prepare(self):
        self.result_changes = {"status": "blocked", "blockers": [{"code": "unsupported_dtb", "reason": "原配 DTB 不符"}]}
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            first = matrix.run(self.plan_ref)
            prepare.reset_mock()
            self.assertEqual(matrix.run(self.plan_ref), first)
        prepare.assert_not_called()
        self.assertEqual(first["status_counts"], {"blocked": 2})

    def test_retryable_failures_do_not_publish_receipts(self):
        failures = [({"status": "blocked", "error": "執行逾時"}, []),
                    ({"status": "blocked", "blockers": [{"code": "reader_failed"}]}, []),
                    ({"status": "blocked", "blockers": [{"code": "io_error"}]}, []),
                    ({"status": "blocked", "blockers": [{"code": "execution_failed"}]}, []),
                    ({"status": "blocked", "blockers": [{"code": "host_tool"}]}, []),
                    ({"status": "blocked"}, [{"status": "error"}]),
                    ({"status": "blocked"}, [{"status": "failed"}])]
        for changes, reads in failures:
            self.configure()
            self.result_changes, self.component_reads = changes, reads
            with self.subTest(changes=changes, reads=reads):
                with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare), self.assertRaises(ValueError):
                    self.execute()
                self.assertFalse((self.directory() / "receipt.json").exists())
                self.assertTrue((self.directory() / "attempt-0001/source/preparation.json").is_file())

    def test_retryable_row_continues_other_boards_and_groups_then_resumes(self):
        other = source_entry(self.source, board="bpi-f2s")
        later = source_entry(self.source, release="jammy")
        self.configure([self.entries[0], other, later])
        failed_source = str(self.source / self.entries[0]["relative_path"])

        def failing(source, sha256, **kwargs):
            result = self.prepare(source, sha256, **kwargs)
            if str(source) == failed_source:
                result.update(status="blocked", error="合成讀取中斷")
                (kwargs["output"] / "preparation.json").write_bytes(matrix.queue.encode(result))
            return result

        with mock.patch.object(matrix.preparation, "prepare", side_effect=failing) as prepare:
            partial = matrix.run(self.plan_ref)
        self.assertEqual(prepare.call_count, 3)
        self.assertEqual(partial["status_counts"], {"retryable": 1, "prepared": 2})
        self.assertEqual([r["image_id"] for r in partial["retryable"]], [self.entries[0]["image_id"]])
        self.assertEqual(len(partial["results"]), 2)
        self.assertFalse((self.directory() / "receipt.json").exists())
        self.assertTrue(all((self.directory(i) / "receipt.json").is_file() for i in (1, 2)))
        self.assertFalse((self.output / "summary.json").exists())
        incomplete = list(self.output.glob("incomplete-*.json"))
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(matrix.queue.read_json(incomplete[0]), partial)
        partial_ref = matrix.review.reference(incomplete[0])
        old_attempt = matrix.artifact_digest(self.directory() / "attempt-0001")
        old_receipts = [matrix.review.reference(self.directory(i) / "receipt.json") for i in (1, 2)]
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            complete = matrix.run(self.plan_ref)
        self.assertEqual(prepare.call_count, 1)
        self.assertTrue(prepare.call_args.kwargs["from_extraction"])
        self.assertEqual(complete["retryable"], [])
        self.assertEqual(complete["status_counts"], {"prepared": 3})
        self.assertEqual(matrix.queue.read_json(self.output / "summary.json"), complete)
        self.assertEqual(matrix.review.reference(incomplete[0]), partial_ref)
        self.assertEqual(matrix.artifact_digest(self.directory() / "attempt-0001"), old_attempt)
        self.assertEqual([matrix.review.reference(self.directory(i) / "receipt.json") for i in (1, 2)], old_receipts)

    def test_repeated_retryable_runs_publish_distinct_incomplete_reports(self):
        with mock.patch.object(matrix.preparation, "prepare", side_effect=OSError("合成暫時讀取失敗")):
            for _ in range(2):
                result = matrix.run(self.plan_ref)
                self.assertEqual(result["status_counts"], {"retryable": 2})
                self.assertEqual(result["results"], [])
                self.assertEqual(result["replayed_only"], 0)
        self.assertEqual(len(list(self.output.glob("incomplete-*.json"))), 2)
        self.assertFalse((self.output / "summary.json").exists())
        self.assertFalse(any((self.directory(i) / "receipt.json").exists() for i in (0, 1)))

    def test_completed_tamper_on_resume_is_retryable_without_second_prepare(self):
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare):
            matrix.run(self.plan_ref)
        summary_ref = matrix.review.reference(self.output / "summary.json")
        (self.directory() / "attempt-0001/source/extraction/file-0000.bin").write_bytes(b"\xff")
        with mock.patch.object(matrix.preparation, "prepare") as prepare:
            result = matrix.run(self.plan_ref)
        prepare.assert_not_called()
        self.assertEqual(result["status_counts"], {"retryable": 1, "prepared": 1})
        self.assertEqual(matrix.review.reference(self.output / "summary.json"), summary_ref)

    def test_publish_json_links_only_complete_fsynced_content(self):
        path, data = self.output / "published.json", {"status": "prepared", "hardware_validated": False}
        events = []
        real_link, real_fsync = os.link, os.fsync

        def syncing(fd):
            real_fsync(fd)
            events.append("sync")

        def linking(source, destination, **kwargs):
            self.assertFalse(path.exists())
            self.assertEqual(matrix.queue.read_json(source), data)
            self.assertGreaterEqual(events.count("sync"), 2)
            real_link(source, destination, **kwargs)
            events.append("link")

        with mock.patch.object(matrix.os, "fsync", side_effect=syncing), \
                mock.patch.object(matrix.os, "link", side_effect=linking):
            matrix.publish_json(path, data)
        self.assertEqual(events[-2:], ["link", "sync"])
        self.assertEqual(matrix.queue.read_json(path), data)

    def test_publish_json_never_overwrites_existing_file_or_symlink(self):
        target = self.root / "existing.json"
        target.write_bytes(b"\x01")
        for linked in (False, True):
            path = self.output / ("linked.json" if linked else "existing.json")
            if linked:
                path.symlink_to(target)
            else:
                path.write_bytes(b"\x01")
            with self.subTest(linked=linked), self.assertRaises(FileExistsError):
                matrix.publish_json(path, {"hardware_validated": False})
            self.assertEqual(path.read_bytes(), b"\x01")
            self.assertEqual(target.read_bytes(), b"\x01")

    def test_interrupted_staging_does_not_publish_partial_receipt(self):
        path = self.output / "receipt.json"

        def interrupted(staging, data):
            staging.write_bytes(b'{"schema":')
            raise OSError("合成寫入中斷")

        with mock.patch.object(matrix.queue, "save_json", side_effect=interrupted), \
                mock.patch.object(matrix.os, "link") as link, self.assertRaises(OSError):
            matrix.publish_json(path, {"status": "prepared"})
        link.assert_not_called()
        self.assertFalse(path.exists())
        matrix.publish_json(path, {"status": "prepared"})
        self.assertEqual(matrix.queue.read_json(path), {"status": "prepared"})

    def test_interrupted_link_does_not_publish_receipt(self):
        path = self.output / "receipt.json"
        with mock.patch.object(matrix.os, "link", side_effect=OSError("合成發布中斷")), self.assertRaises(OSError):
            matrix.publish_json(path, {"status": "prepared"})
        self.assertFalse(path.exists())
        staging = list(self.output.glob(".publish-*.json"))
        self.assertEqual(len(staging), 1)
        self.assertEqual(matrix.queue.read_json(staging[0]), {"status": "prepared"})

    def test_interrupted_attempt_preserved_and_retry_uses_new_directory(self):
        self.result_changes = {"status": "blocked", "error": "讀取中斷"}
        self.extraction_changes = {"ok": False}
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            with self.assertRaises(ValueError):
                self.execute()
            old = matrix.artifact_digest(self.directory() / "attempt-0001")
            self.result_changes, self.extraction_changes = {}, {}
            result = self.execute()
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(Path(result["attempt"]).name, "attempt-0002")
        self.assertEqual(matrix.artifact_digest(self.directory() / "attempt-0001"), old)

    def test_blocked_with_incomplete_extraction_has_no_receipt(self):
        self.result_changes = {"status": "blocked", "blockers": [{"code": "unsupported_dtb"}]}
        self.extraction_changes = {"ok": False}
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare), self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.directory() / "receipt.json").exists())

    def test_partial_receipt_is_rejected_without_overwrite(self):
        self.directory().mkdir()
        path = self.directory() / "receipt.json"
        path.write_bytes(b'{"schema":')
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            self.execute()
        prepare.assert_not_called()
        self.assertEqual(path.read_bytes(), b'{"schema":')

    def test_run_rejects_other_runner_and_releases_lock_after_error(self):
        with matrix.lock(self.output), mock.patch.object(matrix.preparation, "prepare") as prepare:
            with self.assertRaises(BlockingIOError):
                matrix.run(self.plan_ref)
        prepare.assert_not_called()
        with mock.patch.object(matrix.preparation, "prepare", side_effect=RuntimeError("合成中斷")):
            with self.assertRaises(RuntimeError):
                matrix.run(self.plan_ref)
        with matrix.lock(self.output):
            pass
        self.assertFalse((self.output / "summary.json").exists())

    def test_lock_symlink_rejected_without_changing_target(self):
        target = self.root / "target"
        target.write_bytes(b"\x01")
        (self.output / "runner.lock").symlink_to(target)
        with self.assertRaises(OSError), matrix.lock(self.output):
            self.fail("符號連結不可成為批次鎖")
        self.assertEqual(target.read_bytes(), b"\x01")

    def test_tools_changed_before_run_rejected_before_prepare(self):
        changed = [{**self.tools[0], "sha256": "b" * 64}]
        with mock.patch.object(matrix, "tool_binding", return_value=changed), \
                mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaisesRegex(ValueError, "工具"):
            matrix.run(self.plan_ref)
        prepare.assert_not_called()
        self.assertEqual(list((self.output / "jobs").iterdir()), [])

    def test_tools_changed_during_group_prevent_summary_and_next_group(self):
        later = source_entry(self.source, release="jammy")
        self.configure([self.entries[0], later])
        changed = [{**self.tools[0], "sha256": "b" * 64}]
        with mock.patch.object(matrix, "tool_binding", side_effect=[self.tools, changed]), \
                mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            with self.assertRaisesRegex(ValueError, "工具"):
                matrix.run(self.plan_ref)
        self.assertEqual(prepare.call_count, 1)
        self.assertFalse(self.directory(1).exists())
        self.assertFalse((self.output / "summary.json").exists())

    def test_low_space_rejected_before_prepare(self):
        with mock.patch.object(matrix.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), \
                mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaisesRegex(ValueError, "空間"):
            matrix.run(self.plan_ref)
        prepare.assert_not_called()

    def test_run_revalidates_limits_source_root_and_offline_flags(self):
        cases = [(field, value) for field, values in (
            ("workers", (0, 5, True, 1.5)),
            ("max_raw_bytes", (0, 33 * 1024**3, True)),
            ("timeout", (0, 86401, True)),
            ("source_root", (str(self.root / "different-source"),)),
            ("hardware_validated", (True, 0, None)),
            ("media_written", (True, 0, None))) for value in values]
        for number, (field, value) in enumerate(cases):
            plan = {**self.plan, field: value}
            ref = matrix.queue.save_json(self.output / f"invalid-{number}.json", plan)
            with self.subTest(field=field, value=value), \
                    mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
                matrix.run(ref)
            prepare.assert_not_called()
        self.assertFalse((self.output / "runner.lock").exists())

    def test_run_rejects_out_of_scope_replay_keys_and_malformed_references(self):
        image_id = self.entries[0]["image_id"]
        cases = [{"unknown-image": self.plan_ref}, [],
                 {image_id: {"path": "relative/extraction.json", "sha256": "a" * 64}},
                 {image_id: {"path": str(self.root / "extraction.json"), "sha256": "bad"}},
                 {image_id: {**self.plan_ref, "extra": True}}]
        for number, replays in enumerate(cases):
            ref = matrix.queue.save_json(self.output / f"invalid-replays-{number}.json",
                                         {**self.plan, "replays": replays})
            with self.subTest(replays=replays), mock.patch.object(matrix.preparation, "prepare") as prepare:
                with self.assertRaises(ValueError):
                    matrix.run(ref)
                prepare.assert_not_called()

    def test_run_rejects_output_outside_scope_at_root_or_inside_source(self):
        for allowed in (self.root / "different-output", self.output):
            with self.subTest(allowed=allowed), mock.patch.object(matrix, "OUTPUT_ROOT", allowed), \
                    mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
                matrix.run(self.plan_ref)
            prepare.assert_not_called()
        ref = matrix.queue.save_json(self.source / "invalid-plan.json", self.plan)
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            matrix.run(ref)
        prepare.assert_not_called()

    def test_run_rejects_rows_not_matching_fixed_catalog(self):
        ref = matrix.queue.save_json(self.output / "invalid-rows.json", {**self.plan, "rows": self.plan["rows"][:1]})
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            matrix.run(ref)
        prepare.assert_not_called()

    def test_four_workers_and_arch_os_group_barrier(self):
        first = [source_entry(self.source, board=board) for board in
                 ("bpi-f2s", "bpi-m1", "bpi-m2b", "bpi-m3")]
        first = [self.entries[0], *first]
        later = [source_entry(self.source, release="jammy"),
                 source_entry(self.source, board="bpi-m5", architecture="arm64")]
        self.configure(sorted(first + later, key=matrix.order))
        original = matrix.execute_row
        mutex, started, release = threading.Lock(), threading.Event(), threading.Event()
        active, peak, entered = 0, 0, []
        finished, errors = [], []
        first_ids = {e["image_id"] for e in first}

        def worker(plan_ref, plan, row, output):
            nonlocal active, peak
            entry = row["entry"]
            key = (entry["architecture"], entry["release"])
            with mutex:
                if entry["image_id"] not in first_ids and not first_ids.issubset(finished):
                    errors.append("前一群組尚未完成")
                if key == ("arm64", "bookworm") and later[0]["image_id"] not in finished:
                    errors.append("前一 OS 群組尚未完成")
                active += 1
                peak = max(peak, active)
                entered.append(entry["image_id"])
                if active == 4:
                    started.set()
            try:
                if entry["image_id"] in first_ids and not release.wait(10):
                    raise AssertionError("等待測試群組放行逾時")
                result = original(plan_ref, plan, row, output)
                with mutex:
                    finished.append(entry["image_id"])
                return result
            finally:
                with mutex:
                    active -= 1

        def runner():
            try:
                matrix.run(self.plan_ref)
            except BaseException as exc:
                errors.append(exc)

        with mock.patch.object(matrix, "execute_row", side_effect=worker), \
                mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare):
            thread = threading.Thread(target=runner)
            thread.start()
            try:
                self.assertTrue(started.wait(10), "四個工作未同時進入")
                with mutex:
                    self.assertEqual(active, 4)
                    self.assertEqual(len(entered), 4)
                    self.assertTrue(set(entered).issubset(first_ids))
            finally:
                release.set()
                thread.join(15)
        self.assertFalse(thread.is_alive(), "批次測試未結束")
        self.assertEqual(errors, [])
        self.assertEqual(peak, 4)
        self.assertEqual(len(finished), 7)

    def test_replay_cross_os_source_rejected_before_prepare(self):
        self.replay(entry=self.entries[1])
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            self.execute()
        prepare.assert_not_called()
        self.assertFalse((self.directory() / "receipt.json").exists())

    def test_replay_index_ignores_cross_source_binding(self):
        self.replay(changes={"source": str(self.source / self.entries[1]["relative_path"])})
        found, ignored = matrix.replay_index({"root": str(self.source), "entries": self.entries},
                                             [self.root / "old-extraction"])
        self.assertEqual(found, {})
        self.assertEqual(sum(ignored.values()), 1)

    def test_replay_index_prioritizes_complete_labels_before_file_count(self):
        root, entry = self.root / "candidates", self.entries[0]
        snapshot(root / "many-files", self.source, entry,
                 files={"/boot/a": b"\x01", "/boot/b": b"\x02"}, missing=("/a", "/b", "/c"),
                 changes={"filesystem_labels_complete": False})
        complete = snapshot(root / "complete-labels", self.source, entry,
                            changes={"filesystem_labels_complete": True})
        snapshot(root / "truthy-labels", self.source, entry,
                 files={"/boot/a": b"\x01", "/boot/b": b"\x02", "/boot/c": b"\x03"},
                 changes={"filesystem_labels_complete": 1})
        found, ignored = matrix.replay_index({"root": str(self.source), "entries": self.entries}, [root])
        self.assertEqual(found, {entry["image_id"]: complete})
        self.assertEqual(ignored, {})

    def test_replay_index_ignores_non_object_manifests(self):
        root = self.root / "non-objects"
        for number, data in enumerate(([], None, "invalid", 1, True)):
            matrix.queue.save_json(root / str(number) / "extraction.json", data)
        found, ignored = matrix.replay_index({"root": str(self.source), "entries": self.entries}, [root])
        self.assertEqual(found, {})
        self.assertEqual(sum(ignored.values()), 5)

    def test_replay_index_breaks_ties_by_files_queries_then_shorter_chain(self):
        root, entry = self.root / "candidates", self.entries[0]
        flags = {"filesystem_labels_complete": True}
        snapshot(root / "few-files", self.source, entry, missing=("/a", "/b", "/c"), changes=flags)
        files = {"/boot/a": b"\x01", "/boot/b": b"\x02"}
        snapshot(root / "few-queries", self.source, entry, files=files, missing=("/a",), changes=flags)
        best = snapshot(root / "z-best", self.source, entry, files=files, missing=("/a", "/b"), changes=flags)
        snapshot(root / "a-replay", self.source, entry, files=files, missing=("/a", "/b"), origin=best, changes=flags)
        found, ignored = matrix.replay_index({"root": str(self.source), "entries": self.entries}, [root])
        self.assertEqual(found, {entry["image_id"]: best})
        self.assertEqual(ignored, {})

    def test_replay_success_does_not_read_full_source(self):
        ref = self.replay()
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            result = self.execute()
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(prepare.call_args.args, (ref["path"], ref["sha256"]))
        self.assertTrue(prepare.call_args.kwargs["from_extraction"])
        self.assertFalse(result["source_reread"])

    def test_incomplete_replay_falls_back_once_to_exact_source(self):
        self.replay()

        def incomplete(source, sha256, **kwargs):
            self.result_changes = ({"status": "blocked", "error": "舊證據未擷取此路徑，不能假定不存在：/boot/new"}
                                   if kwargs["from_extraction"] else {})
            return self.prepare(source, sha256, **kwargs)

        with mock.patch.object(matrix.preparation, "prepare", side_effect=incomplete) as prepare:
            result = self.execute()
        self.assertEqual([call.kwargs["from_extraction"] for call in prepare.call_args_list], [True, False])
        self.assertEqual(prepare.call_args.args, (str(self.source / self.entries[0]["relative_path"]),
                                                 self.entries[0]["expected_sha256"]))
        self.assertEqual([a["mode"] for a in result["attempts"]], ["replay", "source"])
        self.assertTrue(result["source_reread"])

    def test_replay_confirmed_block_does_not_fall_back(self):
        self.replay()
        self.result_changes = {"status": "blocked", "blockers": [{"code": "unsupported_dtb"}]}
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            result = self.execute()
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["source_reread"])

    def test_corrupt_snapshot_file_or_diagnostic_never_falls_back(self):
        ref = self.replay()
        for name in ("file-0000.bin", "query-0000.stderr"):
            path = Path(ref["path"]).parent / name
            old = path.read_bytes()
            path.write_bytes(b"\xff")
            with self.subTest(name=name), mock.patch.object(matrix.preparation, "prepare") as prepare:
                with self.assertRaises(ValueError):
                    self.execute()
                prepare.assert_not_called()
                self.assertFalse((self.directory() / "receipt.json").exists())
            path.write_bytes(old)

    def test_corrupt_snapshot_manifest_never_falls_back(self):
        ref = self.replay()
        path = Path(ref["path"])
        path.write_bytes(path.read_bytes() + b" ")
        with mock.patch.object(matrix.preparation, "prepare") as prepare, self.assertRaises(ValueError):
            self.execute()
        prepare.assert_not_called()

    def test_corrupt_ancestor_snapshot_never_falls_back(self):
        original = snapshot(self.root / "ancestor", self.source, self.entries[0])
        replay = snapshot(self.root / "replayed", self.source, self.entries[0], origin=original)
        for name in ("file-0000.bin", "query-0000.stderr"):
            self.configure(replays={self.entries[0]["image_id"]: replay})
            path = Path(original["path"]).parent / name
            old = path.read_bytes()
            path.write_bytes(b"\xff")
            with self.subTest(name=name), mock.patch.object(matrix.preparation, "prepare") as prepare:
                with self.assertRaises(ValueError):
                    self.execute()
                prepare.assert_not_called()
                self.assertFalse((self.directory() / "receipt.json").exists())
            path.write_bytes(old)

    def test_snapshot_component_path_escape_rejected(self):
        ref = self.replay()
        data = matrix.review.ref_data(ref)
        data["files"]["/boot/synthetic.bin"]["file"] = "../outside.bin"
        changed = matrix.queue.save_json(self.root / "escaped.json", data)
        with self.assertRaisesRegex(ValueError, "越界"):
            matrix.snapshot_integrity(changed)

    def test_completed_extraction_from_interruption_is_replayed(self):
        previous = self.directory() / "attempt-0001"
        snapshot(previous / "source/extraction", self.source, self.entries[0])
        before = matrix.artifact_digest(previous)
        with mock.patch.object(matrix.preparation, "prepare", side_effect=self.prepare) as prepare:
            result = self.execute()
        self.assertEqual(prepare.call_count, 1)
        self.assertTrue(prepare.call_args.kwargs["from_extraction"])
        self.assertEqual(Path(result["attempt"]).name, "attempt-0002")
        self.assertEqual(matrix.artifact_digest(previous), before)

    def test_cli_accepts_plan_sha256(self):
        with mock.patch.object(matrix, "run", return_value={"hardware_validated": False}) as run:
            result = matrix.main(["run", "--plan", self.plan_ref["path"], "--plan-sha256", self.plan_ref["sha256"]])
        self.assertEqual(result, 0)
        run.assert_called_once_with(self.plan_ref)

    def test_cli_retryable_result_exits_nonzero(self):
        with mock.patch.object(matrix, "run", return_value={"retryable": [{"status": "retryable"}]}):
            result = matrix.main(["run", "--plan", self.plan_ref["path"], "--plan-sha256", self.plan_ref["sha256"]])
        self.assertEqual(result, 2)


class MatrixToolFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def capture(self, family, suffix):
        directory = self.root / (family + suffix)
        if family == "amlogic":
            return amlogic._Capture(mock.Mock(), directory, "bananapim5", "6.6.1-current-meson64")
        capture = special.Capture(mock.Mock(), directory, {"blockers": [], "reads": [], "hardware_validated": False})
        self.addCleanup(os.close, capture.fd)
        return capture

    def check_retryable(self, capture, suffix):
        self.assertEqual(len(capture.manifest["blockers"]), 1)
        self.assertEqual(capture.manifest["blockers"][0].get("code"), "execution_failed")
        directory = self.root / ("result-" + suffix)
        matrix.queue.save_json(directory / "family-result.json", capture.manifest)
        result = {"status": "blocked", "blockers": capture.manifest["blockers"]}
        self.assertTrue(matrix.read_failed(result, directory))

    def test_actual_capture_attempt_marks_host_os_errors_retryable(self):
        for family in ("amlogic", "special"):
            with self.subTest(family=family):
                capture = self.capture(family, "-oserror")
                action = mock.Mock(side_effect=OSError("合成主機工具無法啟動"))
                self.assertIsNone(capture.attempt("host_tool", action))
                action.assert_called_once_with()
                self.check_retryable(capture, family)

    def test_actual_run_and_capture_attempt_mark_timeouts_and_os_errors_retryable(self):
        for family in ("amlogic", "special"):
            for number, error in enumerate((subprocess.TimeoutExpired(["dtc"], 1), OSError("合成工具缺失"))):
                suffix = family + str(number)
                with self.subTest(family=family, error=type(error).__name__):
                    capture = self.capture(family, "-run-" + str(number))
                    with mock.patch.object(amlogic.subprocess, "run", side_effect=error) as run:
                        self.assertIsNone(capture.attempt("dtc", lambda: amlogic._run(["dtc", "--version"])))
                    run.assert_called_once()
                    self.check_retryable(capture, suffix)

    def test_deterministic_parser_block_is_not_a_host_execution_failure(self):
        for family in ("amlogic", "special"):
            with self.subTest(family=family):
                capture = self.capture(family, "-parse")
                action = mock.Mock(side_effect=amlogic.AmlogicError("合成原配格式不符"))
                self.assertIsNone(capture.attempt("kernel", action))
                self.assertNotEqual(capture.manifest["blockers"][0].get("code"), "execution_failed")
                directory = self.root / ("result-" + family)
                matrix.queue.save_json(directory / "family-result.json", capture.manifest)
                self.assertFalse(matrix.read_failed({"status": "blocked", "blockers": capture.manifest["blockers"]}, directory))


if __name__ == "__main__":
    unittest.main()
