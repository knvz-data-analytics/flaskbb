"""New unit tests for flaskbb.forum.forms (Tarefa 1.3 - Parte 1).

These tests target flaskbb/forum/forms.py, which had 0% coverage in
the Parte 1 baseline for the flaskbb/forum/ module.
"""

from werkzeug.datastructures import MultiDict

from flaskbb.forum.forms import (
    EditTopicForm,
    PostForm,
    ReplyForm,
    SearchPageForm,
    TopicForm,
)


class TestPostForm:
    def test_save_creates_post_with_submitted_content(
        self, database, post_request_context, topic, user
    ):
        """Happy path: PostForm.save() persists a new post with the
        content typed into the form."""
        form = PostForm(
            formdata=MultiDict({"content": "Hello there!"}), meta={"csrf": False}
        )

        post = form.save(user, topic)

        assert post.content == "Hello there!" # type: ignore

    def test_content_is_required(self, database, post_request_context):
        """Error path: submitting an empty content field must fail
        validation instead of silently accepting an empty post."""
        form = PostForm(formdata=MultiDict({"content": ""}), meta={"csrf": False})

        assert not form.validate_on_submit()


class TestTopicForm:
    def test_save_tracks_topic_when_requested(
        self, database, post_request_context, forum, user
    ):
        """Happy path: creating a topic with 'track_topic' checked
        should make the author start tracking the new topic."""
        form = TopicForm(
            formdata=MultiDict(
                {
                    "title": "New Topic",
                    "content": "Topic content",
                    "track_topic": "y",
                }
            ),
            meta={"csrf": False},
        )

        topic = form.save(user, forum)

        assert user.is_tracking_topic(topic)


class TestReplyForm:
    def test_save_untracks_topic_when_not_requested(
        self, database, post_request_context, topic, user
    ):
        """Edge case: if the user was already tracking the topic but
        submits a reply with 'track_topic' unchecked, the topic must
        be untracked (the 'else' branch of ReplyForm.save)."""
        user.track_topic(topic)
        user.save()

        form = ReplyForm(
            formdata=MultiDict({"content": "A reply"}), meta={"csrf": False}
        )
        form.save(user, topic)

        assert not user.is_tracking_topic(topic)


class TestEditTopicForm:
    def test_populate_obj_updates_topic_and_post_together(
        self, database, post_request_context, topic
    ):
        """Edge case: EditTopicForm.populate_obj accepts several
        objects at once and must update the topic title and the
        first post's content from the same submitted data."""
        form = EditTopicForm(
            formdata=MultiDict(
                {"title": "Edited Title", "content": "Edited content"}
            ),
            obj=topic.first_post,
            meta={"csrf": False},
        )

        form.populate_obj(topic, topic.first_post)

        assert (topic.title, topic.first_post.content) == (
            "Edited Title",
            "Edited content",
        )


class TestSearchPageForm:
    def test_requires_at_least_one_search_type(self, database, post_request_context):
        """Error path: submitting a query without selecting any of
        the search_types checkboxes must fail validation."""
        form = SearchPageForm(
            formdata=MultiDict({"search_query": "flask"}), meta={"csrf": False}
        )

        assert not form.validate_on_submit()
