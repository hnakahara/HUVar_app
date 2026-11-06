"""バッチジョブの有効期限（解析終了から）とアセンブリ照合。"""
import random
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from . import assembly_check as ac
from .models import AnalysisJob

PASSWORD = "correct-horse-battery-42"


def _make_fastas(root: Path):
    """2 つの「アセンブリ」の FASTA。chr1 は両者で同一（実際の chr1 先頭付近のように
    判別できない領域）、chr17 は互いに異なる配列。"""
    import pysam
    rng = random.Random(7)
    shared_chr1 = "".join(rng.choice("ACGT") for _ in range(6000))
    paths = {}
    for asm in ac.ASSEMBLIES:
        p = root / f"{asm}.fa"
        with p.open("w") as fh:
            for chrom in ("chr1", "chr17"):
                seq = shared_chr1 if chrom == "chr1" else \
                    "".join(rng.choice("ACGT") for _ in range(6000))
                fh.write(f">{chrom}\n")
                for i in range(0, len(seq), 60):
                    fh.write(seq[i:i + 60] + "\n")
        pysam.faidx(str(p))
        paths[asm] = p
    return paths


def _records(fa, chrom, n, prefix="chr"):
    out = []
    for i in range(n):
        pos = 100 + i * 37
        ref = fa.fetch(chrom, pos - 1, pos)
        alt = "A" if ref != "A" else "C"
        name = chrom if prefix else chrom.removeprefix("chr")
        out.append(f"{name}\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t.")
    return out


def _vcf_bytes(fasta_path: Path, n=60, header_lines=(), chrom_prefix="chr",
               chrom="chr17", leading_shared=0):
    """``leading_shared`` 件の chr1（両アセンブリで同一）の後に ``chrom`` の ``n`` 件。"""
    import pysam
    fa = pysam.FastaFile(str(fasta_path))
    lines = ["##fileformat=VCFv4.2", *header_lines,
             "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"]
    lines += _records(fa, "chr1", leading_shared, chrom_prefix)
    lines += _records(fa, chrom, n, chrom_prefix)
    fa.close()
    return ("\n".join(lines) + "\n").encode()


class AssemblyCheckTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.paths = _make_fastas(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def _upload(self, data: bytes, name="x.vcf"):
        return SimpleUploadedFile(name, data)

    def test_ref_match_detects_the_build(self):
        f = self._upload(_vcf_bytes(self.paths["GRCh37"]))
        ev = ac.gather_evidence(f, self.paths)
        self.assertEqual(ev.match["GRCh37"], (60, 60))
        self.assertLess(ev.rate("GRCh38"), 0.5)
        self.assertEqual(ac.mismatch_reason("GRCh38", ev)[0], "GRCh37")
        self.assertIsNone(ac.mismatch_reason("GRCh37", ev))
        self.assertEqual(f.read(2), b"##")            # rewound for saving

    def test_gzip_and_bare_chromosome_names(self):
        import gzip
        data = gzip.compress(_vcf_bytes(self.paths["GRCh38"], chrom_prefix=""))
        found = ac.check_assembly("GRCh37", self._upload(data, "x.vcf.gz"), self.paths)
        self.assertEqual(found[0], "GRCh38")

    def test_header_used_only_when_content_is_inconclusive(self):
        hdr = ["##contig=<ID=chr1,length=249250621>"]      # GRCh37 chr1
        self.assertEqual(ac.assembly_from_header(hdr)[0], "GRCh37")
        self.assertEqual(ac.assembly_from_header(["##reference=file:///ref/hg38.fa"])[0],
                         "GRCh38")
        # too few records to compare -> the header decides
        few = _vcf_bytes(self.paths["GRCh38"], n=5, header_lines=hdr)
        found = ac.check_assembly("GRCh38", self._upload(few), self.paths)
        self.assertEqual(found[0], "GRCh37")
        self.assertIn("ヘッダー", found[1])
        # enough records: content wins over a wrong header
        many = _vcf_bytes(self.paths["GRCh38"], n=60, header_lines=hdr)
        self.assertIsNone(ac.check_assembly("GRCh38", self._upload(many), self.paths))

    def test_regression_start_of_file_cannot_tell_builds_apart(self):
        """先頭が両アセンブリで同一の領域に偏った VCF（GATK hg19 の実例と同じ形）。"""
        hdr = ["##contig=<ID=chr1,length=249250621>", "##contig=<ID=chr2,length=243199373>"]
        # (a) 後半に判別できる変異がある: ファイル全体から抽出するので中身で判定できる
        data = _vcf_bytes(self.paths["GRCh37"], n=150, leading_shared=150)
        found = ac.check_assembly("GRCh38", self._upload(data), self.paths)
        self.assertEqual(found[0], "GRCh37")
        self.assertIn("REF", found[1])
        # (b) 判別できる変異が無い: ヘッダーの染色体長で止める（以前は素通り）
        data = _vcf_bytes(self.paths["GRCh37"], n=0, leading_shared=150, header_lines=hdr)
        found = ac.check_assembly("GRCh38", self._upload(data), self.paths)
        self.assertEqual(found[0], "GRCh37")
        self.assertIn("249,250,621", found[1])
        self.assertIsNone(ac.check_assembly("GRCh37", self._upload(data), self.paths))
        # (c) 選択側の FASTA しか無くても、ヘッダーで止める
        found = ac.check_assembly("GRCh38", self._upload(data), {"GRCh38": self.paths["GRCh38"]})
        self.assertEqual(found[0], "GRCh37")
        # (d) ヘッダーも無く判別できなければ止めない
        data = _vcf_bytes(self.paths["GRCh37"], n=0, leading_shared=150)
        self.assertIsNone(ac.check_assembly("GRCh38", self._upload(data), self.paths))

    def test_sample_covers_the_whole_file(self):
        data = _vcf_bytes(self.paths["GRCh38"], n=500, leading_shared=2000)
        _, recs = ac.read_vcf_sample(self._upload(data))
        self.assertEqual(len(recs), ac.MAX_RECORDS)
        self.assertGreater(sum(1 for c, _, _ in recs if c == "chr17"), 20)

    def test_no_reference_available_never_blocks(self):
        f = self._upload(_vcf_bytes(self.paths["GRCh37"]))
        self.assertIsNone(ac.check_assembly("GRCh38", f, {}))

    def test_only_selected_reference_available(self):
        f = self._upload(_vcf_bytes(self.paths["GRCh37"]))
        found = ac.check_assembly("GRCh38", f, {"GRCh38": self.paths["GRCh38"]})
        self.assertEqual(found[0], "GRCh37")

    def test_form_and_api_refuse_a_mismatch(self):
        from .forms import BatchVariantForm
        paths = self.paths
        with mock.patch.object(ac, "_fasta_path", side_effect=lambda a: paths.get(a)):
            data = _vcf_bytes(paths["GRCh37"])
            form = BatchVariantForm({"assembly": "GRCh38", "mode": "germline",
                                     "insilico_tool": "esm1b"},
                                    {"vcf": self._upload(data)})
            self.assertFalse(form.is_valid())
            self.assertIn("GRCh37", str(form.errors["assembly"]))
            ok = BatchVariantForm({"assembly": "GRCh37", "mode": "germline",
                                   "insilico_tool": "esm1b"}, {"vcf": self._upload(data)})
            self.assertTrue(ok.is_valid(), ok.errors)

            from rest_framework.authtoken.models import Token
            cache.clear()
            user = User.objects.create_user("api", "api@example.org", PASSWORD)
            token = Token.objects.create(user=user)
            auth = {"HTTP_AUTHORIZATION": f"Token {token.key}"}
            resp = self.client.post(reverse("api:job_create"),
                                    {"vcf": self._upload(data), "assembly": "GRCh38"}, **auth)
            self.assertEqual(resp.status_code, 400)
            self.assertIn("GRCh37", resp.json()["error"])
            user.refresh_from_db()
            self.assertEqual(user.api_batch_remaining, user.api_batch_monthly_limit)
            resp = self.client.post(reverse("api:job_create"),
                                    {"vcf": self._upload(data), "assembly": "hg99"}, **auth)
            self.assertEqual(resp.status_code, 400)
            with mock.patch("api.views.run_batch_classification") as task:
                resp = self.client.post(reverse("api:job_create"),
                                        {"vcf": self._upload(data), "assembly": "GRCh37"},
                                        **auth)
            self.assertEqual(resp.status_code, 201, resp.content)
            task.delay.assert_called_once()


class JobExpiryTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.user = User.objects.create_user("bob", "bob@example.org", PASSWORD)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def _job(self, status, **kw):
        return AnalysisJob.objects.create(
            owner=self.user, kind=AnalysisJob.Kind.BATCH, status=status,
            input_file=SimpleUploadedFile("in.vcf", b"##fileformat=VCFv4.2\n"), **kw)

    def test_expiry_is_set_when_the_analysis_finishes(self):
        from .tasks import run_batch_classification
        job = self._job(AnalysisJob.Status.PENDING,
                        expires_at=timezone.now() - timedelta(hours=5))

        def fake(in_path, out_path, *a, **k):
            Path(out_path).write_text("ok\n")
        with mock.patch("analysis.cache.cached_classify_batch", side_effect=fake):
            run_batch_classification(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, AnalysisJob.Status.DONE)
        delta = job.expires_at - timezone.now()
        self.assertGreater(delta, timedelta(minutes=55))
        self.assertLessEqual(delta, timedelta(hours=1))

    def test_failed_jobs_also_get_an_expiry(self):
        from .tasks import run_batch_classification
        job = self._job(AnalysisJob.Status.PENDING)
        with mock.patch("analysis.cache.cached_classify_batch", side_effect=ValueError("bad")):
            run_batch_classification(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, AnalysisJob.Status.FAILED)
        self.assertGreater(job.expires_at, timezone.now() + timedelta(minutes=55))

    def test_cleanup_keeps_queued_and_running_jobs(self):
        from .tasks import cleanup_expired_jobs
        past = timezone.now() - timedelta(hours=3)
        queued = self._job(AnalysisJob.Status.PENDING, expires_at=past)
        running = self._job(AnalysisJob.Status.RUNNING, expires_at=past)
        done = self._job(AnalysisJob.Status.DONE, expires_at=past)
        fresh = self._job(AnalysisJob.Status.DONE,
                          expires_at=timezone.now() + timedelta(minutes=30))
        stale = self._job(AnalysisJob.Status.PENDING, expires_at=past)
        AnalysisJob.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=8))
        done_input = Path(done.input_file.path)
        self.assertEqual(cleanup_expired_jobs(), 2)
        left = set(AnalysisJob.objects.values_list("pk", flat=True))
        self.assertEqual(left, {queued.pk, running.pk, fresh.pk})
        self.assertFalse(done_input.exists())

    def test_api_status_reports_expiry_only_after_finishing(self):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.user)
        job = self._job(AnalysisJob.Status.RUNNING)
        url = reverse("api:job_status", args=[job.id])
        auth = {"HTTP_AUTHORIZATION": f"Token {token.key}"}
        self.assertIsNone(self.client.get(url, **auth).json()["expires_at"])
        AnalysisJob.objects.filter(pk=job.pk).update(status=AnalysisJob.Status.FAILED)
        self.assertIsNotNone(self.client.get(url, **auth).json()["expires_at"])

    def test_pages_show_expiry_after_the_analysis(self):
        self.user.mfa_exempt = True
        self.user.save()
        self.client.force_login(self.user)
        running = self._job(AnalysisJob.Status.RUNNING)
        page = self.client.get(reverse("analysis:batch_status", args=[running.id]))
        self.assertContains(page, "解析終了の 1 時間後")
        self.assertContains(self.client.get(reverse("analysis:batch_list")), "解析終了の 1 時間後")


class JobTimeLimitTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.user = User.objects.create_user("carl", "carl@example.org", PASSWORD)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def _job(self, status=AnalysisJob.Status.PENDING, **kw):
        return AnalysisJob.objects.create(
            owner=self.user, kind=AnalysisJob.Kind.BATCH, status=status,
            input_file=SimpleUploadedFile("in.vcf", b"##fileformat=VCFv4.2\n"), **kw)

    def test_redelivered_or_duplicate_job_is_not_run_again(self):
        from .tasks import run_batch_classification
        for status in (AnalysisJob.Status.RUNNING, AnalysisJob.Status.DONE):
            job = self._job(status)
            with mock.patch("analysis.cache.cached_classify_batch") as engine:
                self.assertIn("skipped", run_batch_classification(job.id))
            engine.assert_not_called()

    @override_settings(BATCH_TIME_LIMIT=1)
    def test_time_limit_stops_child_processes_and_marks_the_job_failed(self):
        import subprocess
        import time as _time
        from .tasks import run_batch_classification
        job = self._job()

        def slow_engine(*a, **k):          # like VEP: a long-running child process
            if subprocess.run(["sleep", "30"]).returncode != 0:
                raise RuntimeError("VEP exited -15")
        t0 = _time.monotonic()
        with mock.patch("analysis.cache.cached_classify_batch", side_effect=slow_engine):
            run_batch_classification(job.id)
        self.assertLess(_time.monotonic() - t0, 15)
        job.refresh_from_db()
        self.assertEqual(job.status, AnalysisJob.Status.FAILED)
        self.assertIn("制限時間", job.error)
        self.assertIsNotNone(job.started_at)
        self.assertGreater(job.expires_at, timezone.now())

    def test_worker_start_marks_orphaned_running_jobs_failed(self):
        from .tasks import _on_worker_ready
        running = self._job(AnalysisJob.Status.RUNNING, started_at=timezone.now())
        pending = self._job()
        _on_worker_ready()
        running.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(running.status, AnalysisJob.Status.FAILED)
        self.assertIn("再起動", running.error)
        self.assertEqual(pending.status, AnalysisJob.Status.PENDING)

    @override_settings(CELERY_TASK_TIME_LIMIT=3600)
    def test_cleanup_fails_jobs_running_far_beyond_the_limit(self):
        from .tasks import cleanup_expired_jobs
        old = self._job(AnalysisJob.Status.RUNNING,
                        started_at=timezone.now() - timedelta(hours=3))
        legacy = self._job(AnalysisJob.Status.RUNNING)         # no started_at (before this fix)
        AnalysisJob.objects.filter(pk=legacy.pk).update(
            created_at=timezone.now() - timedelta(hours=3))
        recent = self._job(AnalysisJob.Status.RUNNING,
                           started_at=timezone.now() - timedelta(minutes=30))
        cleanup_expired_jobs()
        states = dict(AnalysisJob.objects.values_list("pk", "status"))
        self.assertEqual(states[old.pk], AnalysisJob.Status.FAILED)
        self.assertEqual(states[legacy.pk], AnalysisJob.Status.FAILED)
        self.assertEqual(states[recent.pk], AnalysisJob.Status.RUNNING)

    def test_broker_redelivery_waits_longer_than_the_time_limit(self):
        from django.conf import settings
        self.assertGreater(settings.CELERY_TASK_TIME_LIMIT, settings.BATCH_TIME_LIMIT)
        self.assertGreater(settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"],
                           settings.CELERY_TASK_TIME_LIMIT)


class BatchWorkersTests(TestCase):
    """HUVAR_BATCH_WORKERS / HUVAR_BATCH_CLASSIFY_WORKERS はバッチだけに効く。"""

    def _built(self, batch, env):
        import os
        import sys
        import types
        captured = {}

        class Config:
            def __init__(self, **kw):
                captured.update(kw)

        enums = types.SimpleNamespace(Assembly=lambda v: v, InSilicoTool=lambda v: v)
        fake = {"acmg_classifier": types.ModuleType("acmg_classifier"),
                "acmg_classifier.config": types.SimpleNamespace(Config=Config),
                "acmg_classifier.models": types.ModuleType("acmg_classifier.models"),
                "acmg_classifier.models.enums": enums}
        from . import engine
        with mock.patch.dict(sys.modules, fake), mock.patch.dict(os.environ, env):
            engine._build_config("GRCh38", batch=batch)
        return captured

    def test_only_batch_uses_the_batch_settings(self):
        env = {"HUVAR_BATCH_WORKERS": "12", "HUVAR_BATCH_CLASSIFY_WORKERS": "4"}
        kw = self._built(True, env)
        self.assertEqual((kw["workers"], kw["classify_workers"]), (12, 4))
        kw = self._built(False, env)
        self.assertNotIn("workers", kw)
        self.assertNotIn("classify_workers", kw)
        self.assertNotIn("workers", self._built(True, {"HUVAR_BATCH_WORKERS": "abc"}))


class BatchTsvNormalisationTests(TestCase):
    def test_input_representation_column_and_warning(self):
        import csv
        from .cache import _write_batch_tsv
        crit = [{"criterion": "PM2", "triggered": False, "strength": "", "evidence": ""}]
        displays = [
            {"variant_id": "chr1:115725557:GA:G", "chrom": "chr1", "pos": 115725557,
             "ref": "GA", "alt": "G", "criteria": crit, "warnings": ["w1"]},
            None,                                         # a record with no result
            {"variant_id": "chr1:10:A:T", "chrom": "chr1", "pos": 10, "ref": "A", "alt": "T",
             "criteria": crit, "warnings": []},
        ]
        out = Path(tempfile.mkdtemp()) / "o.tsv"
        _write_batch_tsv(str(out), displays,
                         inputs=["chr1:115725557:GAAA:GAA", "chr1:5:C:G", ""])
        with out.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        self.assertEqual(rows[0]["input_variant"], "chr1:115725557:GAAA:GAA")
        self.assertTrue(rows[0]["warnings"].startswith("NORMALIZED: VCF allele "
                                                       "chr1:115725557:GAAA:GAA"))
        self.assertTrue(rows[0]["warnings"].endswith(" / w1"))
        self.assertEqual(rows[1]["variant_id"], "chr1:10:A:T")      # stays aligned
        self.assertEqual(rows[1]["input_variant"], "")
        shutil.rmtree(out.parent, ignore_errors=True)

    def test_sample_rows_and_inference_columns(self):
        import csv
        from types import SimpleNamespace
        from . import cache
        crit = [{"criterion": "PP3", "triggered": True, "strength": "Strong", "evidence": "",
                 "values": {"insilico_tool": "FuncVEP-CTI", "insilico_score": "0.9300"}},
                {"criterion": "BA1", "triggered": False, "strength": "", "evidence": "",
                 "values": {"af_value": "0.015"}}]
        d = {"variant_id": "chr1:100:A:G", "chrom": "chr1", "pos": 100, "ref": "A", "alt": "G",
             "criteria": crit, "warnings": []}
        calls = [SimpleNamespace(sample="blood", depth=10, allele_count=4, vaf=0.4),
                 SimpleNamespace(sample="tumor", depth=40, allele_count=10, vaf=0.25)]
        out = Path(tempfile.mkdtemp()) / "o.tsv"
        cols = (["insilico_tool", "insilico_score", "af_value"],
                {"insilico_tool": ("PP3", "BP4"), "insilico_score": ("PP3", "BP4"),
                 "af_value": ("BA1", "BS1")})
        with mock.patch.object(cache, "_inference_columns", return_value=cols):
            cache._write_batch_tsv(str(out), [d, dict(d, variant_id="chr1:5:C:T")],
                                   inputs=["", ""], calls=[calls, []])
        with out.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        self.assertEqual([(r["variant_id"], r["sample"], r["depth"], r["allele_count"], r["vaf"])
                          for r in rows],
                         [("chr1:100:A:G", "blood", "10", "4", "0.4"),
                          ("chr1:100:A:G", "tumor", "40", "10", "0.25"),
                          ("chr1:5:C:T", "", "", "", "")])
        self.assertNotIn("gt", rows[0])
        self.assertNotIn("dp", rows[0])
        self.assertEqual((rows[0]["insilico_tool"], rows[0]["insilico_score"], rows[0]["af_value"]),
                         ("FuncVEP-CTI", "0.9300", "0.015"))
        self.assertEqual(rows[0]["PP3"], "met")
        shutil.rmtree(out.parent, ignore_errors=True)
