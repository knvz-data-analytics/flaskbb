"""New unit tests for flaskbb.forum.views (Tarefa 1.3 - Parte 1).

These tests target moderation actions in flaskbb/forum/views.py, which
had only 7% coverage in the Parte 1 baseline for the flaskbb/forum/
module.
"""

from flask import g
from flask_login import login_user

from flaskbb.forum import views


class TestLockTopicView:
    def test_moderator_locks_topic(self, database, application, moderator_user, topic):
        """Happy path: a moderator of the topic's forum posting to
        the lock endpoint must lock the topic."""
        view = views.LockTopic.as_view("lock_topic")

        with application.test_request_context(f"/topic/{topic.id}/lock", method="POST"):
            # `current_forum` memoizes onto `flask.g`, which is not
            # reset between tests sharing the package-scoped app
            # context, so clear it to avoid a stale forum from a
            # previous test leaking in here.
            for name in ("post", "topic", "forum", "category"):
                g.pop(name, None)
            login_user(moderator_user)
            view(topic_id=topic.id)

        assert topic.locked is True


class TestHidePostView:
    def test_hiding_an_already_hidden_post_does_not_hide_it_again(
        self, database, application, super_moderator_user, topic
    ):
        """Edge case: posting to the hide endpoint for a post that is
        already hidden must be a no-op (guarded by the 'post.hidden'
        check) instead of hiding it a second time."""
        post = topic.first_post
        post.hide(topic.user)
        post.save()
        hidden_by_before = post.hidden_by

        view = views.HidePost.as_view("hide_post")

        with application.test_request_context(f"/post/{post.id}/hide", method="POST"):
            login_user(super_moderator_user)
            view(post_id=post.id)

        assert post.hidden_by == hidden_by_before
