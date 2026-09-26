"""Tests for ManageForum.post() (Parte 2, refactor #4).

flaskbb/forum/views.py::ManageForum.post() had 0% coverage at the end
of Parte 1 -- it's the module's biggest hotspot (8 branches in one
method) and the highest-risk refactor in the Parte 2 plan. These
tests exercise every bulk action against the real dispatch table
introduced by the refactor, so a mistake in `_BULK_ACTIONS` (wrong
field, wrong `reverse`, or a typo in a key) would fail loudly here
instead of silently breaking one action in production.
"""

import pytest
from flask import g
from flask_login import login_user

from flaskbb.forum import views
from flaskbb.forum.models import Topic


def _post_manage_forum(application, forum, topic, moderator_user, form_data):
    view = views.ManageForum.as_view("manage_forum")
    with application.test_request_context(
        f"/forum/{forum.id}/edit", method="POST", data=form_data
    ):
        # current_forum (flaskbb/forum/locals.py) memoizes onto
        # flask.g, which isn't reset between tests sharing the
        # package-scoped `application` fixture -- see
        # parte1/NOVOS_TESTES.md for the same issue in Parte 1.
        for name in ("post", "topic", "forum", "category"):
            g.pop(name, None)
        login_user(moderator_user)
        return view(forum_id=forum.id)


class TestManageForumBulkActions:
    @pytest.mark.parametrize(
        ("action", "attr", "expected_before", "expected_after"),
        [
            pytest.param("lock", "locked", False, True, id="lock"),
            pytest.param("highlight", "important", False, True, id="highlight"),
        ],
    )
    def test_simple_bulk_action_flips_the_expected_flag(
        self,
        database,
        application,
        moderator_user,
        topic,
        action,
        attr,
        expected_before,
        expected_after,
    ):
        assert getattr(topic, attr) == expected_before

        _post_manage_forum(
            application,
            topic.forum,
            topic,
            moderator_user,
            {"rowid": str(topic.id), action: "1"},
        )

        assert getattr(topic, attr) == expected_after

    @pytest.mark.parametrize(
        ("action", "attr", "start_value"),
        [
            pytest.param("unlock", "locked", True, id="unlock"),
            pytest.param("trivialize", "important", True, id="trivialize"),
        ],
    )
    def test_simple_bulk_action_reverses_the_expected_flag(
        self, database, application, moderator_user, topic, action, attr, start_value
    ):
        setattr(topic, attr, start_value)
        topic.save()

        _post_manage_forum(
            application,
            topic.forum,
            topic,
            moderator_user,
            {"rowid": str(topic.id), action: "1"},
        )

        assert getattr(topic, attr) is False

    def test_delete_action_removes_the_topic(
        self, database, application, moderator_user, topic
    ):
        topic_id = topic.id

        _post_manage_forum(
            application,
            topic.forum,
            topic,
            moderator_user,
            {"rowid": str(topic_id), "delete": "1"},
        )

        from flaskbb.extensions import db

        assert db.session.get(Topic, topic_id) is None

    def test_hide_then_unhide_action_round_trips(
        self, database, application, super_moderator_user, topic
    ):
        # do_topic_action()'s "hide"/"unhide" branches additionally
        # require Has("makehidden"), which the plain "Moderator" group
        # doesn't have by default (see parte1/PLANO_TESTES.md) -- so
        # this one needs super_moderator_user, unlike the other
        # bulk actions above.
        _post_manage_forum(
            application,
            topic.forum,
            topic,
            super_moderator_user,
            {"rowid": str(topic.id), "hide": "1"},
        )
        assert topic.hidden is True

        _post_manage_forum(
            application,
            topic.forum,
            topic,
            super_moderator_user,
            {"rowid": str(topic.id), "unhide": "1"},
        )
        assert topic.hidden is False

    def test_move_action_moves_the_topic_to_the_new_forum(
        self, database, application, moderator_user, super_moderator_user, topic, forum
    ):
        from flaskbb.forum.models import Forum

        other_forum = Forum(title="Other forum", category_id=forum.category_id)
        other_forum.save()
        other_forum.moderators.append(moderator_user)

        _post_manage_forum(
            application,
            forum,
            topic,
            super_moderator_user,
            {"rowid": str(topic.id), "move": "1", "forum": str(other_forum.id)},
        )

        assert topic.forum_id == other_forum.id

    def test_unknown_action_leaves_the_topic_untouched(
        self, database, application, moderator_user, topic
    ):
        assert topic.locked is False

        _post_manage_forum(
            application,
            topic.forum,
            topic,
            moderator_user,
            {"rowid": str(topic.id), "something_else": "1"},
        )

        assert topic.locked is False

    def test_no_topics_selected_leaves_state_untouched(
        self, database, application, moderator_user, topic
    ):
        assert topic.locked is False

        _post_manage_forum(
            application, topic.forum, topic, moderator_user, {"lock": "1"}
        )

        assert topic.locked is False
