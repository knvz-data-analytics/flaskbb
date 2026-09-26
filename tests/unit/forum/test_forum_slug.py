"""New parametrized unit test for flaskbb.forum.models (Tarefa 1.4 - Parte 1).

Covers Forum.slug generation (flaskbb/forum/models.py) across several
title shapes, mixing valid and invalid/degenerate inputs.
"""

import pytest

from flaskbb.forum.models import Forum


class TestForumSlug:
    @pytest.mark.parametrize(
        ("title", "expected_slug"),
        [
            pytest.param(
                "General Discussion", "general-discussion", id="valid-simple-title"
            ),
            pytest.param(
                "Café Central", "cafe-central", id="valid-accented-characters"
            ),
            pytest.param(
                "C++ Programming",
                "c++-programming",
                id="valid-punctuation-kept-mid-word",
            ),
            pytest.param(
                "2024 Announcements!!!",
                "2024-announcements",
                id="valid-trailing-punctuation-stripped",
            ),
            pytest.param(
                "   ", "", id="invalid-whitespace-only-title-collapses-to-empty-slug"
            ),
        ],
    )
    def test_slug_generation(self, title, expected_slug):
        """Forum.slug must slugify the title consistently across
        plain, accented, punctuated and degenerate (whitespace-only)
        titles."""
        forum = Forum(title=title) # type: ignore

        assert forum.slug == expected_slug
