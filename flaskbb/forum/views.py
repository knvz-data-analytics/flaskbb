# -*- coding: utf-8 -*-
"""
flaskbb.forum.views
~~~~~~~~~~~~~~~~~~~

This module handles the forum logic like creating and viewing
topics and posts.

:copyright: (c) 2014 by the FlaskBB Team.
:license: BSD, see LICENSE for more details.
"""

import logging
import math

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    request,
    url_for,
)
from flask.views import MethodView
from flask_allows2 import And, Permission
from flask_babelplus import gettext as _
from flask_login import current_user, login_required
from pluggy import HookimplMarker
from sqlalchemy import asc, desc

from flaskbb.extensions import allows, db, pluggy
from flaskbb.forum.forms import (
    EditTopicForm,
    NewTopicForm,
    QuickreplyForm,
    ReplyForm,
    ReportForm,
    SearchPageForm,
    UserSearchForm,
)
from flaskbb.forum.models import (
    Category,
    Forum,
    ForumsRead,
    Post,
    Topic,
    TopicsRead,
    topictracker,
)
from flaskbb.markup import make_renderer
from flaskbb.user.models import User
from flaskbb.utils.helpers import (
    FlashAndRedirect,
    do_topic_action,
    format_quote,
    get_online_users,
    real,
    register_view,
    render_template,
    time_diff,
    time_utcnow,
)
from flaskbb.utils.queries import first_or_404, paginate
from flaskbb.utils.requirements import (
    CanAccessForum,
    CanDeletePost,
    CanDeleteTopic,
    CanEditPost,
    CanPostReply,
    CanPostTopic,
    Has,
    IsAtleastModeratorInForum,
)
from flaskbb.utils.settings import flaskbb_config

from .locals import current_category, current_forum, current_topic
from .utils import force_login_if_needed

impl = HookimplMarker("flaskbb")

logger = logging.getLogger(__name__)


class ForumIndex(MethodView):
    def get(self):
        categories = Category.get_all(user=real(current_user))

        # Fetch a few stats about the forum
        user_count = db.session.scalar(db.select(db.func.count(User.id)))
        topic_count = db.session.scalar(db.select(db.func.count(Topic.id)))
        post_count = db.session.scalar(db.select(db.func.count(Post.id)))
        newest_user = db.session.scalar(db.select(User).order_by(User.id.desc()))

        # Check if we use redis or not
        if not current_app.config["REDIS_ENABLED"]:
            online_users = db.session.scalar(
                db.select(db.func.count(User.id)).where(User.lastseen >= time_diff())
            )

            # Because we do not have server side sessions,
            # we cannot check if there are online guests
            online_guests = None
        else:
            online_users = len(get_online_users())
            online_guests = len(get_online_users(guest=True))

        return render_template(
            "forum/index.html",
            categories=categories,
            user_count=user_count,
            topic_count=topic_count,
            post_count=post_count,
            newest_user=newest_user,
            online_users=online_users,
            online_guests=online_guests,
        )


class ViewCategory(MethodView):
    def get(self, category_id: int, slug: str | None = None):
        category, forums = Category.get_forums(
            category_id=category_id, user=real(current_user)
        )

        return render_template("forum/category.html", forums=forums, category=category)


class ViewForum(MethodView):
    decorators = [
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that forum"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        )
    ]

    def get(self, forum_id: int, slug: str | None = None):
        page = request.args.get("page", 1, type=int)

        forum_instance, forumsread = Forum.get_forum(
            forum_id=forum_id, user=real(current_user)
        )

        if forum_instance.external:
            return redirect(forum_instance.external)

        topics = Forum.get_topics(
            forum_id=forum_instance.id,
            user=real(current_user),
            page=page,
            per_page=flaskbb_config["TOPICS_PER_PAGE"],
        )

        return render_template(
            "forum/forum.html",
            forum=forum_instance,
            topics=topics,
            forumsread=forumsread,
        )


class ViewPost(MethodView):
    decorators = [
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that topic"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        )
    ]

    def get(self, post_id: int):
        """Redirects to a post in a topic."""
        post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
        post_in_topic = db.session.scalar(
            db.select(db.func.count(Post.id)).where(
                Post.topic_id == post.topic_id, Post.id <= post_id
            )
        )
        page = int(math.ceil(post_in_topic / float(flaskbb_config["POSTS_PER_PAGE"])))

        url_kwargs = {
            "topic_id": post.topic.id,
            "page": page,
            "_anchor": f"pid{post.id}",
        }
        if post.topic.slug:
            url_kwargs["slug"] = post.topic.slug

        return redirect(url_for("forum.view_topic", **url_kwargs))


class ViewTopic(MethodView):
    decorators = [
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that topic"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        )
    ]

    def get(self, topic_id: int, slug: str | None = None):
        page = request.args.get("page", 1, type=int)

        # Fetch some information about the topic
        topic = Topic.get_topic(topic_id, hiddencheck=True)

        topic.views += 1
        topic.save()

        # Update the topicsread status if the user hasn't read it
        forumsread = None
        if current_user.is_authenticated:
            forumsread = ForumsRead.get_for_user(current_user.id, topic.forum_id)

        topic.update_read(real(current_user), topic.forum, forumsread)

        posts = Topic.get_posts(topic_id, page)

        if len(posts.items) == 0:
            abort(404)

        return render_template(
            "forum/topic.html",
            topic=topic,
            posts=posts,
            last_seen=time_diff(),
            form=self.form(),
        )

    @allows.requires(
        CanPostReply,
        on_fail=FlashAndRedirect(
            message=_("You are not allowed to post a reply to this topic."),
            level="warning",
            endpoint=lambda *a, **k: url_for(
                "forum.view_topic",
                topic_id=k["topic_id"],
            ),
        ),
    )
    def post(self, topic_id: int, slug: str | None = None):
        topic = Topic.get_topic(topic_id, hiddencheck=True)
        form = self.form()

        if not form:
            flash(_("Cannot post reply"), "warning")
            return redirect(topic.url)

        elif form.validate_on_submit():
            post = form.save(real(current_user), topic)
            return redirect(post.url)

        else:
            for e in form.errors.get("content", []):
                flash(e, "danger")
            return redirect(topic.url)

    def form(self):
        if Permission(CanPostReply):
            return QuickreplyForm()
        return None


class NewTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            CanPostTopic,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to post a topic here"),
                level="warning",
                endpoint=lambda *a, **k: current_forum.url,
            ),
        ),
    ]

    def get(self, forum_id: int, slug: str | None = None):
        forum_instance = first_or_404(db.select(Forum).where(Forum.id == forum_id))
        return render_template(
            "forum/new_topic.html",
            forum=forum_instance,
            form=self.form(),
            edit_mode=False,
        )

    def post(self, forum_id: int, slug: str | None = None):
        forum_instance = first_or_404(db.select(Forum).where(Forum.id == forum_id))
        form = self.form()
        if form.validate_on_submit():
            topic = form.save(real(current_user), forum_instance)
            return redirect(topic.url)

        return render_template(
            "forum/new_topic.html",
            forum=forum_instance,
            form=form,
            edit_mode=False,
        )

    def form(self):
        pluggy.hook.flaskbb_form_topic(form=NewTopicForm)
        return NewTopicForm()


class EditTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanPostTopic,
            CanEditPost,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to edit that topic"),
                level="warning",
                endpoint=lambda *a, **k: current_forum.url,
            ),
        ),
    ]

    def get(self, topic_id: int, slug: str | None = None):
        topic = Topic.get_topic(topic_id, hiddencheck=True)
        form = self.form(obj=topic.first_post, title=topic.title)
        form.track_topic.data = current_user.is_tracking_topic(topic)

        return render_template(
            "forum/new_topic.html", forum=topic.forum, form=form, edit_mode=True
        )

    def post(self, topic_id: int, slug: str | None = None):
        topic = Topic.get_topic(topic_id, hiddencheck=True)
        post = topic.first_post
        form = self.form(obj=post, title=topic.title)

        if form.validate_on_submit():
            form.populate_obj(topic, post)
            topic = form.save(real(current_user), topic.forum)

            return redirect(topic.url)

        return render_template(
            "forum/new_topic.html", forum=topic.forum, form=form, edit_mode=True
        )

    def form(self, **kwargs):
        pluggy.hook.flaskbb_form_topic(form=NewTopicForm)
        return EditTopicForm(**kwargs)


# Maps a bulk-action form key to (do_topic_action's `action`,
# `reverse`, and a function that builds the localized flash message).
# Each of the 7 "simple" bulk actions in ManageForum.post() used to
# repeat the same do_topic_action() + flash() + redirect() shape by
# hand; extracting it here removes that duplication. `move` is
# deliberately NOT in this table -- it has its own extra validation
# and permission check, so it stays as an explicit branch.
_BULK_ACTIONS = {
    "lock": ("locked", False, lambda count: _("%(count)s topics locked.", count=count)),
    "unlock": ("locked", True, lambda count: _("%(count)s topics unlocked.", count=count)),
    "highlight": (
        "important",
        False,
        lambda count: _("%(count)s topics highlighted.", count=count),
    ),
    "trivialize": (
        "important",
        True,
        lambda count: _("%(count)s topics trivialized.", count=count),
    ),
    "delete": ("delete", False, lambda count: _("%(count)s topics deleted.", count=count)),
    "hide": ("hide", False, lambda count: _("%(count)s topics hidden.", count=count)),
    "unhide": ("unhide", False, lambda count: _("%(count)s topics unhidden.", count=count)),
}


def _apply_bulk_topic_action(action: str, topics: list, mod_forum_url: str):
    """Apply one of the simple bulk topic actions from `_BULK_ACTIONS`
    and build the flash + redirect response for it."""
    field, reverse, message_for = _BULK_ACTIONS[action]
    changed = do_topic_action(
        topics=topics, user=real(current_user), action=field, reverse=reverse
    )
    flash(message_for(changed), "success")
    return redirect(mod_forum_url)


class ManageForum(MethodView):
    decorators = [
        login_required,
        allows.requires(
            IsAtleastModeratorInForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to manage this forum"),
                level="danger",
                endpoint=lambda *a, **k: url_for(
                    "forum.view_forum",
                    forum_id=k["forum_id"],
                ),
            ),
        ),
    ]

    def get(self, forum_id: int, slug: str | None = None):
        forum_instance, forumsread = Forum.get_forum(
            forum_id=forum_id, user=real(current_user)
        )

        if forum_instance.external:
            return redirect(forum_instance.external)

        # remove the current forum from the select field (move).
        available_forums = (
            db.session.execute(db.select(Forum).order_by(Forum.position))
            .unique()
            .scalars()
            .all()
        )
        available_forums.remove(forum_instance)  # pyright: ignore
        page = request.args.get("page", 1, type=int)
        topics = Forum.get_topics(
            forum_id=forum_instance.id,
            user=real(current_user),
            page=page,
            per_page=flaskbb_config["TOPICS_PER_PAGE"],
        )

        return render_template(
            "forum/edit_forum.html",
            forum=forum_instance,
            topics=topics,
            available_forums=available_forums,
            forumsread=forumsread,
        )

    def post(self, forum_id: int, slug: str | None = None):
        forum_instance, __ = Forum.get_forum(forum_id=forum_id, user=real(current_user))
        mod_forum_url = url_for(
            "forum.manage_forum", forum_id=forum_instance.id, slug=forum_instance.slug
        )

        topic_ids = request.form.getlist("rowid")
        selected_topics = (
            db.session.execute(db.select(Topic).where(Topic.id.in_(topic_ids)))
            .scalars()
            .all()
        )

        if not len(selected_topics) > 0:
            flash(
                _(
                    "In order to perform this action you have to select at "
                    "least one topic."
                ),
                "danger",
            )
            return redirect(mod_forum_url)

        for action in _BULK_ACTIONS:
            if action in request.form:
                return _apply_bulk_topic_action(action, selected_topics, mod_forum_url)

        # moving has its own extra validation/permission check, so it
        # isn't part of the generic _BULK_ACTIONS table above.
        if "move" in request.form:
            new_forum_id = request.form.get("forum", type=int)

            if not new_forum_id:
                flash(_("Please choose a new forum for the topics."), "info")
                return redirect(mod_forum_url)

            new_forum = first_or_404(db.select(Forum).where(Forum.id == new_forum_id))

            # check the permission in the current forum and in the new forum
            if not Permission(
                And(
                    IsAtleastModeratorInForum(forum_id=new_forum_id),
                    IsAtleastModeratorInForum(forum=forum_instance),
                )
            ):
                flash(
                    _("You do not have the permissions to move this topic."), "danger"
                )
                return redirect(mod_forum_url)

            if new_forum.move_topics_to(selected_topics):
                flash(_("Topics moved."), "success")
            else:
                flash(_("Failed to move topics."), "danger")

            return redirect(mod_forum_url)

        flash(_("Unknown action requested"), "danger")
        return redirect(mod_forum_url)


class NewPost(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            CanPostReply,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to post a reply"),
                level="warning",
                endpoint=lambda *a, **k: url_for(
                    "forum.view_topic",
                    topic_id=k["topic_id"],
                ),
            ),
        ),
    ]

    def get(self, topic_id: int, slug: str | None = None, post_id: int | None = None):
        topic = Topic.get_topic(topic_id, hiddencheck=True)
        form = self.form()
        form.track_topic.data = current_user.is_tracking_topic(topic)

        if post_id is not None:
            post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
            form.content.data = format_quote(post.username, post.content)

        return render_template("forum/new_post.html", topic=topic, form=form)

    def post(self, topic_id: int, slug: str | None = None, post_id: int | None = None):
        topic = Topic.get_topic(topic_id, hiddencheck=True)
        form = self.form()

        # check if topic exists
        if post_id is not None:
            post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)

        if form.validate_on_submit():
            post = form.save(real(current_user), topic)
            return redirect(post.url)

        return render_template("forum/new_post.html", topic=topic, form=form)

    def form(self):
        pluggy.hook.flaskbb_form_post(form=ReplyForm)
        return ReplyForm()


class EditPost(MethodView):
    decorators = [
        allows.requires(
            CanEditPost,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to edit that post"),
                level="danger",
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
        login_required,
    ]

    def get(self, post_id: int):
        post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)

        if post.is_first_post():
            return redirect(url_for("forum.edit_topic", topic_id=post.topic_id))

        form = self.form(obj=post)
        form.track_topic.data = current_user.is_tracking_topic(post.topic)

        return render_template(
            "forum/new_post.html", topic=post.topic, form=form, edit_mode=True
        )

    def post(self, post_id: int):
        post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
        form = self.form(obj=post)

        if form.validate_on_submit():
            form.populate_obj(post)
            post = form.save(real(current_user), post.topic)
            return redirect(post.url)

        return render_template(
            "forum/new_post.html", topic=post.topic, form=form, edit_mode=True
        )

    def form(self, **kwargs):
        pluggy.hook.flaskbb_form_post(form=ReplyForm)
        return ReplyForm(**kwargs)


class ReportView(MethodView):
    decorators = [login_required]
    form = ReportForm

    def get(self, post_id: int):
        return render_template("forum/report_post.html", form=self.form())

    def post(self, post_id: int):
        form = self.form()
        if form.validate_on_submit():
            post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
            form.save(real(current_user), post)
            flash(_("Thanks for reporting."), "success")

        return render_template("forum/report_post.html", form=form)


class MemberList(MethodView):
    form = UserSearchForm

    def _resolve_sort(self, sort_by: str, order_by: str):
        """Translate the 'sort_by'/'order_by' query params into the
        SQLAlchemy ordering function and column to sort by. Shared by
        get() and post(), which previously duplicated this exact
        logic."""
        order_func = asc if order_by == "asc" else desc

        if sort_by == "reg_date":
            sort_obj = User.id
        elif sort_by == "post_count":
            sort_obj = User.post_count
        else:
            sort_obj = User.username

        return order_func, sort_obj

    def get(self):
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort_by", "reg_date")
        order_by = request.args.get("order_by", "asc")
        order_func, sort_obj = self._resolve_sort(sort_by, order_by)

        users = db.paginate(
            db.select(User).order_by(order_func(sort_obj)),
            page=page,
            per_page=flaskbb_config["USERS_PER_PAGE"],
            error_out=False,
        )
        return render_template(
            "forum/memberlist.html", users=users, search_form=self.form()
        )

    def post(self):
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort_by", "reg_date")
        order_by = request.args.get("order_by", "asc")
        order_func, sort_obj = self._resolve_sort(sort_by, order_by)

        form = self.form()
        if form.validate():
            users = form.get_results().paginate(
                page=page, per_page=flaskbb_config["USERS_PER_PAGE"], error_out=False
            )
            return render_template(
                "forum/memberlist.html", users=users, search_form=form
            )

        users = db.paginate(
            db.select(User).order_by(order_func(sort_obj)),
            page=page,
            per_page=flaskbb_config["USERS_PER_PAGE"],
            error_out=False,
        )
        return render_template("forum/memberlist.html", users=users, search_form=form)


class TopicTracker(MethodView):
    decorators = [login_required]

    def get(self):
        page = request.args.get("page", 1, type=int)
        stmt = (
            db.select(Topic, Post, TopicsRead, ForumsRead)
            .where(
                db.and_(
                    topictracker.c.topic_id == Topic.id,
                    topictracker.c.user_id == current_user.id,
                )
            )
            .outerjoin(
                TopicsRead,
                db.and_(
                    TopicsRead.topic_id == Topic.id,
                    TopicsRead.user_id == current_user.id,
                ),
            )
            .outerjoin(Post, Topic.last_post_id == Post.id)
            .outerjoin(Forum, Topic.forum_id == Forum.id)
            .outerjoin(
                ForumsRead,
                db.and_(
                    ForumsRead.forum_id == Forum.id,
                    ForumsRead.user_id == current_user.id,
                ),
            )
            .where()
            .order_by(Topic.last_updated.desc())
        )

        topics = paginate(stmt, page=page)

        return render_template("forum/topictracker.html", topics=topics)

    def post(self):
        topic_ids = request.form.getlist("rowid")
        tmp_topics = (
            db.session.execute(db.select(Topic).filter(Topic.id.in_(topic_ids)))
            .scalars()
            .all()
        )

        for topic in tmp_topics:
            real(current_user).untrack_topic(topic)

        real(current_user).save()

        flash(
            _("%(topic_count)s topics untracked.", topic_count=len(tmp_topics)),
            "success",
        )
        return redirect(url_for("forum.topictracker"))


class Search(MethodView):
    form = SearchPageForm

    def get(self):
        return render_template("forum/search_form.html", form=self.form())

    def post(self):
        form = self.form()
        if form.validate_on_submit():
            result = form.get_results()
            return render_template("forum/search_result.html", form=form, result=result)

        return render_template("forum/search_form.html", form=form)


class DeleteTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanDeleteTopic,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to delete this topic"),
                level="danger",
                # TODO(anr): consider the referrer -- for now, back to topic
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = first_or_404(db.select(Topic).where(Topic.id == topic_id), hidden_check=True)
        topic.delete()
        return redirect(url_for("forum.view_forum", forum_id=topic.forum_id))


def _set_topic_flag(topic_id: int, attr_name: str, value: bool) -> Topic:
    """Fetch a topic, set one boolean flag on it, and persist it.

    Shared by the single-topic moderation toggles below (lock/unlock,
    highlight/trivialize), which previously each repeated the same
    fetch -> mutate -> save sequence by hand.
    """
    topic = first_or_404(db.select(Topic).where(Topic.id == topic_id), hidden_check=True)
    setattr(topic, attr_name, value)
    topic.save()
    return topic


class LockTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            IsAtleastModeratorInForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to lock this topic"),
                level="danger",
                # TODO(anr): consider the referrer -- for now, back to topic
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = _set_topic_flag(topic_id, "locked", True)
        return redirect(topic.url)


class UnlockTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            IsAtleastModeratorInForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to unlock this topic"),
                level="danger",
                # TODO(anr): consider the referrer -- for now, back to topic
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = _set_topic_flag(topic_id, "locked", False)
        return redirect(topic.url)


class HighlightTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            IsAtleastModeratorInForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to highlight this topic"),
                level="danger",
                # TODO(anr): consider the referrer -- for now, back to topic
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = _set_topic_flag(topic_id, "important", True)
        return redirect(topic.url)


class TrivializeTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            IsAtleastModeratorInForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to trivialize this topic"),
                level="danger",
                # TODO(anr): consider the referrer -- for now, back to topic
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, topic_id: int | None = None, slug: str | None = None):
        topic = _set_topic_flag(topic_id, "important", False)
        return redirect(topic.url)


class DeletePost(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanDeletePost,
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to delete this post"),
                level="danger",
                endpoint=lambda *a, **k: current_topic.url,
            ),
        ),
    ]

    def post(self, post_id: int):
        post: Post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
        topic_url = post.topic.url
        forum_url = post.topic.forum.url

        post.delete()

        # If the post was the first post in the topic, redirect to the forums
        if post.is_first_post():
            return redirect(forum_url)
        return redirect(topic_url)


class RawPost(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that forum"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        ),
    ]

    def get(self, post_id: int):
        post = first_or_404(db.select(Post).where(Post.id == post_id), hidden_check=True)
        return format_quote(username=post.username, content=post.content)


class MarkRead(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that forum"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        ),
    ]

    def post(self, forum_id: int | None = None, slug: str | None = None):
        # Mark a single forum as read
        if forum_id is not None:
            forum_instance = first_or_404(db.select(Forum).where(Forum.id == forum_id))
            forumsread = db.session.execute(
                db.select(ForumsRead).where(
                    ForumsRead.user_id == real(current_user).id,
                    ForumsRead.forum_id == forum_instance.id,
                )
            ).scalar()

            db.session.execute(
                db.delete(TopicsRead).where(
                    TopicsRead.user_id == real(current_user).id,
                    TopicsRead.forum_id == forum_instance.id,
                )
            )

            if not forumsread:
                forumsread = ForumsRead()
                forumsread.user = real(current_user)
                forumsread.forum = forum_instance

            forumsread.last_read = time_utcnow()
            forumsread.cleared = time_utcnow()

            db.session.add(forumsread)
            db.session.commit()

            flash(
                _("Forum %(forum)s marked as read.", forum=forum_instance.title),
                "success",
            )

            return redirect(forum_instance.url)

        # Mark all forums as read

        db.session.execute(
            db.delete(ForumsRead).where(ForumsRead.user_id == real(current_user).id)
        )
        db.session.execute(
            db.delete(TopicsRead).where(TopicsRead.user_id == real(current_user).id)
        )

        forums = db.session.execute(db.select(Forum)).scalars()
        forumsread_list = []
        for forum_instance in forums:
            forumsread = ForumsRead()
            forumsread.user = real(current_user)
            forumsread.forum = forum_instance
            forumsread.last_read = time_utcnow()
            forumsread.cleared = time_utcnow()
            forumsread_list.append(forumsread)

        db.session.add_all(forumsread_list)
        db.session.commit()

        flash(_("All forums marked as read."), "success")

        return redirect(url_for("forum.index"))


class WhoIsOnline(MethodView):
    def get(self):
        if current_app.config["REDIS_ENABLED"]:
            online_users = get_online_users()
        else:
            online_users = db.session.scalars(
                db.select(User).where(User.lastseen >= time_diff())
            )
        return render_template("forum/online_users.html", online_users=online_users)


class TrackTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that forum"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = first_or_404(db.select(Topic).where(Topic.id == topic_id), hidden_check=True)
        real(current_user).track_topic(topic)
        real(current_user).save()
        return redirect(topic.url)


class UntrackTopic(MethodView):
    decorators = [
        login_required,
        allows.requires(
            CanAccessForum(),
            on_fail=FlashAndRedirect(
                message=_("You are not allowed to access that forum"),
                level="warning",
                endpoint=lambda *a, **k: current_category.url,
            ),
        ),
    ]

    def post(self, topic_id: int, slug: str | None = None):
        topic = first_or_404(db.select(Topic).where(Topic.id == topic_id), hidden_check=True)
        real(current_user).untrack_topic(topic)
        real(current_user).save()
        return redirect(topic.url)


def _require_hide_permission(forum: Forum, denied_message: str, redirect_url: str):
    """Check the 'can hide/unhide' permission shared by the four views
    below and, if it's denied, build the flash + redirect response the
    caller should return immediately. Returns None when allowed.

    `denied_message` is passed in (already wrapped in a literal
    `_(...)` call by each caller) rather than composed here, so babel
    can still find and extract each distinct translatable string.
    """
    if not Permission(Has("makehidden"), IsAtleastModeratorInForum(forum=forum)):
        flash(denied_message, "danger")
        return redirect(redirect_url)
    return None


def _redirect_if_already(condition: bool, message: str, redirect_url: str):
    """If `condition` is True, build the flash + redirect response for
    'there is nothing to do here' and return it; otherwise return
    None. Centralizing this guard (instead of repeating it per view)
    is what fixes the missing-`return` bug that used to live in
    UnhidePost.post().
    """
    if condition:
        flash(message, "warning")
        return redirect(redirect_url)
    return None


class HideTopic(MethodView):
    decorators = [login_required]

    def post(self, topic_id: int, slug: str | None = None):
        topic = first_or_404(db.select(Topic).where(Topic.id == topic_id))

        denied = _require_hide_permission(
            topic.forum,
            _("You do not have permission to hide this topic"),
            topic.url,
        )
        if denied:
            return denied

        topic.hide(user=current_user)
        topic.save()

        if Permission(Has("viewhidden")):
            return redirect(topic.url)
        return redirect(topic.forum.url)


class UnhideTopic(MethodView):
    decorators = [login_required]

    def post(self, topic_id: int, slug: str | None = None):
        topic = first_or_404(db.select(Topic).where(Topic.id == topic_id), hidden_check=True)

        denied = _require_hide_permission(
            topic.forum,
            _("You do not have permission to unhide this topic"),
            topic.url,
        )
        if denied:
            return denied

        topic.unhide()
        topic.save()
        return redirect(topic.url)


class HidePost(MethodView):
    decorators = [login_required]

    def post(self, post_id: int):
        post = first_or_404(db.select(Post).where(Post.id == post_id))

        denied = _require_hide_permission(
            post.topic.forum,
            _("You do not have permission to hide this post"),
            post.topic.url,
        )
        if denied:
            return denied

        already = _redirect_if_already(
            post.hidden, _("Post is already hidden"), post.topic.url
        )
        if already:
            return already

        post.hide(current_user)
        post.save()

        if post.is_first_post():
            flash(_("Topic hidden"), "success")
        else:
            flash(_("Post hidden"), "success")

        if post.is_first_post() and not Permission(Has("viewhidden")):
            return redirect(post.topic.forum.url)
        return redirect(post.topic.url)


class UnhidePost(MethodView):
    decorators = [login_required]

    def post(self, post_id: int):
        post = first_or_404(db.select(Post).where(Post.id == post_id))

        denied = _require_hide_permission(
            post.topic.forum,
            _("You do not have permission to unhide this post"),
            post.topic.url,
        )
        if denied:
            return denied

        already = _redirect_if_already(
            not post.hidden, _("Post is already unhidden"), post.topic.url
        )
        if already:
            return already

        post.unhide()
        post.save()
        flash(_("Post unhidden"), "success")
        return redirect(post.topic.url)


class MarkdownPreview(MethodView):
    def post(self, mode: str | None = None):
        text = request.data.decode("utf-8")

        if mode == "nonpost":
            render_classes = pluggy.hook.flaskbb_load_nonpost_markdown_class(
                app=current_app
            )
        else:
            render_classes = pluggy.hook.flaskbb_load_post_markdown_class(
                app=current_app
            )

        renderer = make_renderer(render_classes)
        preview = renderer(text)
        return preview


@impl(tryfirst=True)
def flaskbb_load_blueprints(app: Flask):
    forum = Blueprint("forum", __name__)
    register_view(
        forum,
        routes=["/category/<int:category_id>", "/category/<int:category_id>-<slug>"],
        view_func=ViewCategory.as_view("view_category"),
    )
    register_view(
        forum,
        routes=["/forum/<int:forum_id>/edit", "/forum/<int:forum_id>-<slug>/edit"],
        view_func=ManageForum.as_view("manage_forum"),
    )
    register_view(
        forum,
        routes=["/forum/<int:forum_id>", "/forum/<int:forum_id>-<slug>"],
        view_func=ViewForum.as_view("view_forum"),
    )
    register_view(
        forum,
        routes=["/<int:forum_id>/markread", "/<int:forum_id>-<slug>/markread"],
        view_func=MarkRead.as_view("markread"),
    )
    register_view(
        forum,
        routes=["/<int:forum_id>/topic/new", "/<int:forum_id>-<slug>/topic/new"],
        view_func=NewTopic.as_view("new_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topic/<int:topic_id>/edit",
            "/topic/<int:topic_id>-<slug>/edit",
        ],
        view_func=EditTopic.as_view("edit_topic"),
    )
    register_view(
        forum, routes=["/memberlist"], view_func=MemberList.as_view("memberlist")
    )
    register_view(
        forum,
        routes=["/post/<int:post_id>/delete"],
        view_func=DeletePost.as_view("delete_post"),
    )
    register_view(
        forum,
        routes=["/post/<int:post_id>/edit"],
        view_func=EditPost.as_view("edit_post"),
    )
    register_view(
        forum, routes=["/post/<int:post_id>/raw"], view_func=RawPost.as_view("raw_post")
    )
    register_view(
        forum,
        routes=["/post/<int:post_id>/report"],
        view_func=ReportView.as_view("report_post"),
    )
    register_view(
        forum, routes=["/post/<int:post_id>"], view_func=ViewPost.as_view("view_post")
    )
    register_view(forum, routes=["/search"], view_func=Search.as_view("search"))
    register_view(
        forum,
        routes=["/topic/<int:topic_id>/delete", "/topic/<int:topic_id>-<slug>/delete"],
        view_func=DeleteTopic.as_view("delete_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topic/<int:topic_id>/highlight",
            "/topic/<int:topic_id>-<slug>/highlight",
        ],
        view_func=HighlightTopic.as_view("highlight_topic"),
    )
    register_view(
        forum,
        routes=["/topic/<int:topic_id>/lock", "/topic/<int:topic_id>-<slug>/lock"],
        view_func=LockTopic.as_view("lock_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topic/<int:topic_id>/post/<int:post_id>/reply",
            "/topic/<int:topic_id>-<slug>/post/<int:post_id>/reply",
        ],
        view_func=NewPost.as_view("reply_post"),
    )
    register_view(
        forum,
        routes=[
            "/topic/<int:topic_id>/post/new",
            "/topic/<int:topic_id>-<slug>/post/new",
        ],
        view_func=NewPost.as_view("new_post"),
    )
    register_view(
        forum,
        routes=["/topic/<int:topic_id>", "/topic/<int:topic_id>-<slug>"],
        view_func=ViewTopic.as_view("view_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topic/<int:topic_id>/trivialize",
            "/topic/<int:topic_id>-<slug>/trivialize",
        ],
        view_func=TrivializeTopic.as_view("trivialize_topic"),
    )
    register_view(
        forum,
        routes=["/topic/<int:topic_id>/unlock", "/topic/<int:topic_id>-<slug>/unlock"],
        view_func=UnlockTopic.as_view("unlock_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topictracker/<int:topic_id>/add",
            "/topictracker/<int:topic_id>-<slug>/add",
        ],
        view_func=TrackTopic.as_view("track_topic"),
    )
    register_view(
        forum,
        routes=[
            "/topictracker/<int:topic_id>/delete",
            "/topictracker/<int:topic_id>-<slug>/delete",
        ],
        view_func=UntrackTopic.as_view("untrack_topic"),
    )
    register_view(
        forum, routes=["/topictracker"], view_func=TopicTracker.as_view("topictracker")
    )
    register_view(forum, routes=["/"], view_func=ForumIndex.as_view("index"))
    register_view(
        forum, routes=["/who-is-online"], view_func=WhoIsOnline.as_view("who_is_online")
    )
    register_view(
        forum,
        routes=["/topic/<int:topic_id>/hide", "/topic/<int:topic_id>-<slug>/hide"],
        view_func=HideTopic.as_view("hide_topic"),
    )
    register_view(
        forum,
        routes=["/topic/<int:topic_id>/unhide", "/topic/<int:topic_id>-<slug>/unhide"],
        view_func=UnhideTopic.as_view("unhide_topic"),
    )
    register_view(
        forum,
        routes=["/post/<int:post_id>/hide"],
        view_func=HidePost.as_view("hide_post"),
    )
    register_view(
        forum,
        routes=["/post/<int:post_id>/unhide"],
        view_func=UnhidePost.as_view("unhide_post"),
    )
    register_view(
        forum,
        routes=["/markdown", "/markdown/<path:mode>"],
        view_func=MarkdownPreview.as_view("markdown_preview"),
    )

    forum.before_request(force_login_if_needed)
    app.register_blueprint(forum, url_prefix=app.config["FORUM_URL_PREFIX"])
