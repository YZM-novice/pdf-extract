"""Thin integration seam: `convert(pdf, out_dir) -> artifacts`.

The MinerU subprocess boundary is injected so the pipeline (unzip -> clean ->
write markdown + images -> temp-dir lifecycle) can be verified without loading
a model. The real end-to-end run lives in `test_e2e.py`.
"""

import pathlib
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import pdf_extract  # noqa: E402


def write_fake_zip(zip_path, markdown, images=None):
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("markdown.md", markdown)
        for name, data in (images or {}).items():
            archive.writestr(f"images/{name}", data)


class FakeRunner:
    """Stand-in for the MinerU subprocess adapter at the `convert` seam."""

    def __init__(
        self,
        markdown="Body paragraph.\n",
        fail=None,
        omit_markdown=False,
        images=None,
    ):
        self.markdown = markdown
        self.fail = fail
        self.omit_markdown = omit_markdown
        self.images = {"fig.jpg": b"jpegbytes"} if images is None else images
        self.calls = []

    def __call__(self, pdf, zip_path, tier, ocr_mode, timeout):
        self.calls.append((pdf, zip_path, tier, ocr_mode, timeout))
        if self.fail is not None:
            raise self.fail
        if self.omit_markdown:
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("images/fig.jpg", b"jpegbytes")
            return
        write_fake_zip(zip_path, self.markdown, self.images)


class ConvertArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")
        self.out = self.tmp / "paper_md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_markdown_and_images(self):
        runner = FakeRunner("Body paragraph.\n\n![](images/fig.jpg)\n")

        result = pdf_extract.convert(self.pdf, self.out, runner=runner)

        md = self.out / "cleaned.md"
        self.assertTrue(md.exists())
        self.assertEqual(
            md.read_text(encoding="utf-8"),
            "Body paragraph.\n\n![](image/fig.jpg)\n",
        )
        self.assertTrue((self.out / "image" / "fig.jpg").exists())
        self.assertNotIn("data:image", md.read_text(encoding="utf-8"))
        self.assertEqual(result["markdown"], str(md))
        self.assertEqual(result["images"], str(self.out / "image"))
        self.assertEqual(result["tier"], "flash")

    def test_copies_only_images_the_markdown_references(self):
        # MinerU writes renders of every object into images/, but links only
        # real figures. Orphan equation/table renders must not be copied.
        runner = FakeRunner(
            "Body.\n\n![](images/fig.jpg)\n",
            images={
                "fig.jpg": b"figure",
                "page_1_equation_3.jpg": b"formula",
                "page_2_table_4.jpg": b"table",
            },
        )

        pdf_extract.convert(self.pdf, self.out, runner=runner)

        copied = sorted(p.name for p in (self.out / "image").iterdir())
        self.assertEqual(copied, ["fig.jpg"])
        text = (self.out / "cleaned.md").read_text(encoding="utf-8")
        self.assertIn("![](image/fig.jpg)", text)

    def test_cleaning_applied_during_convert(self):
        runner = FakeRunner("Body.\n\n## References\n\n[1] Foo.\n")

        pdf_extract.convert(self.pdf, self.out, runner=runner)

        text = (self.out / "cleaned.md").read_text(encoding="utf-8")
        self.assertIn("Body.", text)
        self.assertNotIn("References", text)

    def test_success_removes_temp_directory(self):
        runner = FakeRunner()

        pdf_extract.convert(self.pdf, self.out, runner=runner)

        _, zip_path, *_ = runner.calls[0]
        self.assertFalse(pathlib.Path(zip_path).parent.exists())

    def test_failure_keeps_temp_directory_and_reports_path(self):
        runner = FakeRunner(fail=pdf_extract.ConversionError("boom"))

        with self.assertRaises(pdf_extract.ConversionError) as ctx:
            pdf_extract.convert(self.pdf, self.out, runner=runner)

        tmp_dir = ctx.exception.tmp_dir
        self.assertTrue(tmp_dir)
        self.assertTrue(pathlib.Path(tmp_dir).is_dir())
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_missing_markdown_in_zip_is_conversion_error(self):
        runner = FakeRunner(omit_markdown=True)

        with self.assertRaises(pdf_extract.ConversionError) as ctx:
            pdf_extract.convert(self.pdf, self.out, runner=runner)
        shutil.rmtree(ctx.exception.tmp_dir, ignore_errors=True)

    def test_write_failure_maps_to_exit_code_3(self):
        self.out.write_text("this path is a file, not a directory")
        runner = FakeRunner()

        with self.assertRaises(pdf_extract.WriteError) as ctx:
            pdf_extract.convert(self.pdf, self.out, runner=runner)

        self.assertEqual(ctx.exception.exit_code, 3)
        shutil.rmtree(ctx.exception.tmp_dir, ignore_errors=True)

    def test_runner_receives_selected_tier_and_ocr_mode(self):
        runner = FakeRunner()

        pdf_extract.convert(self.pdf, self.out, tier="standard", ocr_mode="ocr", runner=runner)

        _, _, tier, ocr_mode, _ = runner.calls[0]
        self.assertEqual(tier, "standard")
        self.assertEqual(ocr_mode, "ocr")


class RewriteImageLinksTest(unittest.TestCase):
    def test_rewrites_the_images_prefix(self):
        md = "![](images/a.jpg)\n![alt](images/b.png)\n"

        self.assertEqual(
            pdf_extract._rewrite_image_links(md, "x_image"),
            "![](x_image/a.jpg)\n![alt](x_image/b.png)\n",
        )

    def test_leaves_other_links_alone(self):
        md = "[ref](https://example.com/images/a.jpg)\n"

        self.assertEqual(pdf_extract._rewrite_image_links(md, "x_image"), md)


class ReferencedImagesTest(unittest.TestCase):
    def test_collects_only_linked_image_paths(self):
        md = (
            "![](images/a.jpg)\n"
            "![alt](images/nested/b.png)\n"
            "![ref][x]\n"
            "[doc](images/not-an-image.md)\n"
        )

        self.assertEqual(
            pdf_extract._referenced_images(md), {"a.jpg", "nested/b.png"}
        )

    def test_no_links_means_no_images(self):
        self.assertEqual(pdf_extract._referenced_images("plain body\n"), set())


if __name__ == "__main__":
    unittest.main()
