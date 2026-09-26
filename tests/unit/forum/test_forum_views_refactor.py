"""Regression test for the Parte 2 refactoring of Hide/Unhide views.

Before the refactor (see parte2/CODE_SMELLS.md, smell #3),
UnhidePost.post() had a `redirect(...)` guard missing its `return`,
so calling unhide on an already-visible post silently re-processed
the action instead of being a no-op. Extracting the shared
`_redirect_if_already` helper (used by both HidePost and UnhidePost)
fixes that, and this test would have failed against the old code.
"""

from flask import get_flashed_messages
from flask_login import login_user

from flaskbb.forum import views


class TestUnhidePostIsIdempotent:
    def test_unhiding_an_already_visible_post_flashes_exactly_one_message(
        self, database, application, super_moderator_user, topic
    ):
        """Post.unhide() is already internally guarded (a no-op when
        not hidden), so the old view-level bug never corrupted data --
        it just fell through and flashed a second, contradictory
        "Post unhidden" success message right after the "already
        unhidden" warning. That's the observable difference this test
        pins down: exactly one flashed message, not two."""
        post = topic.first_post
        assert post.hidden is False

        view = views.UnhidePost.as_view("unhide_post")
        with application.test_request_context(
            f"/post/{post.id}/unhide", method="POST"
        ):
            login_user(super_moderator_user)
            view(post_id=post.id)

            messages = get_flashed_messages(with_categories=True)

        assert [category for category, _msg in messages] == ["warning"]
