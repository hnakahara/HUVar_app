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
    """互いに異なる配列を持つ 2 つの「アセンブリ」の FASTA（chr1, chr17）。"""
    import pysam
    rng = random.Random(7)
    paths = {}
    for asm in ac.ASSEMBLIES:
        p = root / f"{asm}.fa"
        with p.open("w") as fh:
            for chrom in ("chr1", "chr17"):
                seq = "".join(rng.choice("ACGT") for _ in range(6000))
                fh.write(f">{chrom}\n")
                for i in range(0, len(seq), 60):
                    fh.write(seq[i:i + 60] + "\n")
        pysam.faidx(str(p))
        paths[asm] = p
    return paths


def _vcf_bytes(fasta_path: Path, n=60, header_lines=(), chrom_prefix="chr"):
    import pysam
    fa = pysam.FastaFile(str(fasta_path))
    lines = ["##fileformat=VCFv4.2", *header_lines,
             "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"]
    for i in range(n):
        pos = 100 + i * 37
        ref = fa.fetch("chr1", pos - 1, pos)
        alt = "A" if ref != "A" else "C"
        chrom = "chr1" if chrom_prefix else "1"
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t.")
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
