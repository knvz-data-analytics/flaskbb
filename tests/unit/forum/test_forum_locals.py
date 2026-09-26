"""New unit tests for flaskbb.forum.locals (Tarefa 1.3 - Parte 1).

These tests target the cascading LocalProxy resolution in
flaskbb/forum/locals.py, which had 38% coverage in the Parte 1
baseline for the flaskbb/forum/ module.
"""

from flask import g

from flaskbb.forum.locals import current_category, current_forum


def _reset_locals_cache():
    """`_get_item` memoizes its result onto `flask.g`. Because the
    `application` fixture pushes a single package-scoped app context,
    `g` is *not* reset between tests in this file/worker, so we clear
    it ourselves to keep each test isolated regardless of run order.
    """
    for name in ("post", "topic", "forum", "category"):
        g.pop(name, None)


class TestLocalsCascade:
    def test_current_forum_resolves_through_current_topic(
        self, database, application, topic
    ):
        """Happy path: when only 'topic_id' is present in the URL,
        current_forum must fall back to current_topic.forum instead
        of returning None."""
        with application.test_request_context(f"/topic/{topic.id}"):
            _reset_locals_cache()
            assert current_forum == topic.forum

    def test_current_category_is_none_without_any_view_args(
        self, database, application
    ):
        """Edge case: hitting a route with no category/forum/topic/post
        id at all (e.g. the forum index) must resolve current_category
        to None instead of raising."""
        with application.test_request_context("/"):
            _reset_locals_cache()
            assert not current_category
