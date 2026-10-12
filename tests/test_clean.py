"""Pure-function seam: `clean(markdown) -> (cleaned_text, stats)`.

These tests exercise the cleaning rules without MinerU, so they are the fast
feedback loop for everything the conversion pipeline does to the raw markdown.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.pdf_extract import clean  # noqa: E402


FOOTNOTE = (
    '<small><span class="docvortex-page-footnote" '
    'data-block-type="page_footnote" style="color:#6b7280">'
    "∗ Corresponding author. E-mail address: "
    "[qiu\\_qingan@163.com](mailto:qiu_qingan@163.com) (Q. Qiu)."
    "</span></small>"
)


class ReferencesCutTest(unittest.TestCase):
    def test_cuts_from_references_heading_to_end_of_document(self):
        md = (
            "# Title\n"
            "\n"
            "Body paragraph one.\n"
            "\n"
            "## References\n"
            "\n"
            "[1] Author A. Alpha. 2020.\n"
            "[2] Author B. Beta. 2021.\n"
        )

        cleaned, stats = clean(md)

        self.assertIn("Body paragraph one.", cleaned)
        self.assertNotIn("References", cleaned)
        self.assertNotIn("Author A", cleaned)
        self.assertTrue(stats["references_cut"])
        self.assertEqual(stats["chars_before"], len(md))
        self.assertEqual(stats["chars_after"], len(cleaned))
        self.assertEqual(stats["chars_removed"], len(md) - len(cleaned))

    def test_bibliography_heading_variant(self):
        md = "Body.\n\n# Bibliography\n\n[1] Foo.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, "Body.\n")
        self.assertTrue(stats["references_cut"])

    def test_bold_references_heading_variant(self):
        md = "Body.\n\n**References**\n\n[1] Foo.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, "Body.\n")
        self.assertTrue(stats["references_cut"])

    def test_inline_word_references_is_not_an_anchor(self):
        md = "This study lists references inline in a normal sentence.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, md)
        self.assertFalse(stats["references_cut"])


class FailSafeTest(unittest.TestCase):
    def test_no_anchor_returns_input_unchanged(self):
        md = "Just body text.\nSecond line.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, md)
        self.assertEqual(stats["lines_removed"], 0)
        self.assertEqual(stats["chars_removed"], 0)

    def test_doi_inside_body_sentence_is_preserved(self):
        md = (
            "This work was funded by the [National Natural Science Foundation]\n"
            "[grant numbers 123](https://doi.org/10.13039/501100001809).\n"
        )

        cleaned, _ = clean(md)

        self.assertIn("National Natural Science Foundation", cleaned)
        self.assertIn("doi.org/10.13039", cleaned)

    def test_isolated_word_is_not_a_page_number(self):
        md = "Body.\n\nResults\n\nMore.\n"

        cleaned, _ = clean(md)

        self.assertIn("Results", cleaned)


class MastheadTest(unittest.TestCase):
    def test_masthead_block_removed(self):
        md = (
            "Contents lists available at ScienceDirect\n"
            "European Journal of Operational Research\n"
            "journal homepage: www.elsevier.com/locate/ejor\n"
            "\n"
            "# Real Title\n"
            "\n"
            "Body.\n"
        )

        cleaned, _ = clean(md)

        self.assertNotIn("Contents lists", cleaned)
        self.assertNotIn("European Journal", cleaned)
        self.assertNotIn("journal homepage", cleaned)
        self.assertIn("# Real Title", cleaned)
        self.assertIn("Body.", cleaned)

    def test_copyright_line_removed(self):
        md = "Body text.\n\n© 2020 Elsevier B.V. All rights reserved.\n\nMore body.\n"

        cleaned, _ = clean(md)

        self.assertNotIn("Elsevier", cleaned)
        self.assertIn("More body.", cleaned)

    def test_doi_line_removed(self):
        md = (
            "Body.\n\n"
            "[https://doi.org/10.1016/j.ejor.2020.11.015](https://doi.org/10.1016/j.ejor.2020.11.015)\n\n"
            "More.\n"
        )

        cleaned, _ = clean(md)

        self.assertNotIn("doi.org", cleaned)
        self.assertIn("More.", cleaned)

    def test_article_history_line_removed(self):
        md = "Body.\n\nReceived 13 July 2020 Accepted 10 November 2020 Available online 23 November 2020\n\nMore.\n"

        cleaned, _ = clean(md)

        self.assertNotIn("Received", cleaned)
        self.assertIn("More.", cleaned)


class PageNumberTest(unittest.TestCase):
    def test_isolated_page_number_removed(self):
        md = "Body one.\n\n12\n\nBody two.\n"

        cleaned, _ = clean(md)

        self.assertNotIn("\n12\n", cleaned)
        self.assertIn("Body one.", cleaned)
        self.assertIn("Body two.", cleaned)

    def test_page_label_removed(self):
        md = "Body one.\n\nPage 3 of 12\n\nBody two.\n"

        cleaned, _ = clean(md)

        self.assertNotIn("Page 3", cleaned)
        self.assertIn("Body two.", cleaned)

    def test_consecutive_numeric_rows_preserved(self):
        md = "Body.\n\n16\n16\n14\n14\n\n"

        cleaned, _ = clean(md)

        self.assertIn("16", cleaned)
        self.assertIn("14", cleaned)

    def test_isolated_number_inside_code_fence_preserved(self):
        md = "Body.\n\n```\n\n12\n\n```\n\nMore.\n"

        cleaned, _ = clean(md)

        self.assertIn("12", cleaned)

    def test_isolated_number_inside_details_preserved(self):
        md = "Body.\n\n<details>\n\n12\n\n</details>\n\nMore.\n"

        cleaned, _ = clean(md)

        self.assertIn("12", cleaned)


class PageFootnoteTest(unittest.TestCase):
    def test_standalone_footnote_line_is_removed(self):
        md = f"Intro paragraph.\n\n{FOOTNOTE}\n\nSecond paragraph.\n"

        cleaned, _ = clean(md)

        self.assertNotIn("page_footnote", cleaned)
        self.assertNotIn("Corresponding author", cleaned)
        self.assertIn("Intro paragraph.", cleaned)
        self.assertIn("Second paragraph.", cleaned)

    def test_multiline_footnote_block_is_swallowed_to_closing_tag(self):
        md = (
            "Intro.\n\n"
            '<small><span class="docvortex-page-footnote" '
            'data-block-type="page_footnote">line one\n'
            "line two\n"
            "line three</span></small>\n\n"
            "Outro.\n"
        )

        cleaned, _ = clean(md)

        self.assertNotIn("line one", cleaned)
        self.assertNotIn("line three", cleaned)
        self.assertIn("Intro.", cleaned)
        self.assertIn("Outro.", cleaned)

    def test_unrelated_small_element_is_preserved(self):
        md = "Keep me.\n\n<small>A real note, not a page footnote.</small>\n"

        cleaned, _ = clean(md)

        self.assertIn("A real note, not a page footnote.", cleaned)


class FrontMatterTest(unittest.TestCase):
    def test_content_before_first_h1_is_removed(self):
        md = (
            "Stochastics and Statistics\n\n"
            "# Optimal inspection and mission abort policies\n\n"
            "Body text.\n"
        )

        cleaned, stats = clean(md)

        self.assertNotIn("Stochastics and Statistics", cleaned)
        self.assertTrue(cleaned.startswith("# Optimal inspection"))
        self.assertIn("Body text.", cleaned)
        self.assertTrue(stats["front_matter_cut"])

    def test_document_without_h1_is_untouched(self):
        md = "Just a paragraph.\n\n## A sub-heading\n\nMore text.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, md)
        self.assertFalse(stats["front_matter_cut"])

    def test_h1_inside_fence_does_not_trigger_front_matter_cut(self):
        md = "Intro.\n\n```\n# not a real heading\n```\n\nOutro.\n"

        cleaned, stats = clean(md)

        self.assertIn("Intro.", cleaned)
        self.assertIn("# not a real heading", cleaned)
        self.assertFalse(stats["front_matter_cut"])

    def test_references_heading_is_not_treated_as_the_title(self):
        # A ``# Bibliography`` H1 is back matter, not the document title; the
        # front-matter rule must not eat the body before the references rule.
        md = "Body.\n\n# Bibliography\n\n[1] Foo.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, "Body.\n")
        self.assertTrue(stats["references_cut"])
        self.assertFalse(stats["front_matter_cut"])


class InvariantTest(unittest.TestCase):
    """Global constraints the cleaning pipeline must always hold."""

    CORPUS = [
        "Plain body.\nSecond line.\n",
        "# Title\n\nBody.\n",
        "Body.\n\n# Bibliography\n\n[1] Foo.\n",
        "Intro.\n\n```\n12\npage_footnote\n# not a heading\n```\n\nOutro.\n",
        "Intro.\n\n<details>\n\n12\n\n</details>\n\nOutro.\n",
        "Contents lists available at ScienceDirect\n"
        "Journal of Foo\n"
        "journal homepage: www.example.com\n\n"
        "# Real Title\n\nBody.\n",
        FOOTNOTE,
    ]

    def test_idempotent(self):
        for doc in self.CORPUS:
            once, _ = clean(doc)
            twice, _ = clean(once)
            self.assertEqual(once, twice, f"not idempotent: {doc!r}")

    def test_title_survives(self):
        md = "junk before the title\n\n# Title\n\nBody.\n"

        cleaned, _ = clean(md)

        self.assertTrue(cleaned.startswith("# Title"))

    def test_body_between_title_and_references_survives(self):
        md = "# Title\n\nAlpha.\n\nBeta.\n\n## References\n\n[1] Foo.\n"

        cleaned, _ = clean(md)

        self.assertIn("Alpha.", cleaned)
        self.assertIn("Beta.", cleaned)

    def test_protected_lines_are_untouched(self):
        md = "Intro.\n\n```\n12\npage_footnote\n# not a heading\n```\n\nOutro.\n"

        cleaned, _ = clean(md)

        self.assertIn("12", cleaned)
        self.assertIn("page_footnote", cleaned)
        self.assertIn("# not a heading", cleaned)

    def test_no_anchor_leaves_region_untouched(self):
        md = "No title here.\n\nJust body.\n"

        cleaned, stats = clean(md)

        self.assertEqual(cleaned, md)
        self.assertFalse(stats["front_matter_cut"])
        self.assertFalse(stats["references_cut"])


class GoldenTest(unittest.TestCase):
    """Freeze the exact cleaning of a full document covering every rule."""

    DOC = (
        "Contents lists available at ScienceDirect\n"
        "Journal of Operational Research\n"
        "journal homepage: www.elsevier.com/locate/ejor\n"
        "\n"
        "# Optimal policies for systems\n"
        "\n"
        "Xian Zhao, Qingan Qiu\n"
        "\n"
        "Received 13 July 2020 Accepted 10 November 2020 Available online 23 November 2020\n"
        "\n"
        "https://doi.org/10.1016/j.ejor.2020.11.015\n"
        "\n"
        "Safety-critical systems are commonly required.\n"
        "\n"
        "© 2020 Elsevier B.V. All rights reserved.\n"
        "\n"
        "12\n"
        "\n"
        "The optimization of abort policies.\n"
        "\n"
        '<small><span class="docvortex-page-footnote" '
        'data-block-type="page_footnote">* Corresponding author.</span></small>\n'
        "\n"
        "# Bibliography\n"
        "\n"
        "[1] Foo.\n"
    )

    EXPECTED = (
        "# Optimal policies for systems\n"
        "\n"
        "Xian Zhao, Qingan Qiu\n"
        "\n"
        "\n"
        "\n"
        "Safety-critical systems are commonly required.\n"
        "\n"
        "\n"
        "\n"
        "The optimization of abort policies.\n"
    )

    def test_full_document_cleaning_is_stable(self):
        cleaned, stats = clean(self.DOC)

        self.assertEqual(cleaned, self.EXPECTED)
        self.assertTrue(stats["front_matter_cut"])
        self.assertTrue(stats["references_cut"])


class StatsTest(unittest.TestCase):
    def test_stats_are_arithmetically_consistent(self):
        md = "a\n\nb\n\nReferences\n\nc\n"

        cleaned, stats = clean(md)

        self.assertEqual(stats["chars_before"] - stats["chars_after"], stats["chars_removed"])
        self.assertEqual(stats["lines_before"] - stats["lines_after"], stats["lines_removed"])
        self.assertGreater(stats["lines_removed"], 0)
        self.assertGreater(stats["chars_removed"], 0)


if __name__ == "__main__":
    unittest.main()
