# -*- coding: utf-8 -*-
"""
flaskbb.forum.locals
~~~~~~~~~~~~~~~~~~~~
Thread local helpers for FlaskBB

:copyright: 2017, the FlaskBB Team
:license: BSD, see license for more details
"""

from typing import Any

from flask import g, request
from sqlalchemy import select
from werkzeug.local import LocalProxy

from flaskbb.extensions import db

from .models import Category, Forum, Post, Topic


@LocalProxy
def current_post() -> Post | None:
    """The ``Post`` for the current request, resolved from a
    ``post_id`` in the URL's view args - or ``None`` if this request
    has no ``post_id`` (most routes don't)."""
    return _get_item(Post, "post_id", "post")


@LocalProxy
def current_topic() -> Topic | None:
    """The ``Topic`` for the current request. Cascades: if
    :data:`current_post` resolved to something, its topic is
    returned; otherwise falls back to a ``topic_id`` in the URL's
    view args. This is why a route that only has ``post_id`` (e.g.
    ``/post/<id>/hide``) still gets a correct ``current_topic``."""
    if current_post:
        return current_post.topic
    return _get_item(Topic, "topic_id", "topic")


@LocalProxy
def current_forum() -> Forum | None:
    """The ``Forum`` for the current request. Cascades from
    :data:`current_topic` the same way that cascades from
    :data:`current_post` - see its docstring."""
    if current_topic:
        return current_topic.forum
    return _get_item(Forum, "forum_id", "forum")


@LocalProxy
def current_category() -> Category | None:
    """The ``Category`` for the current request. Last link of the
    ``current_post`` -> ``current_topic`` -> ``current_forum`` ->
    ``current_category`` cascade."""
    if current_forum:
        return current_forum.category
    return _get_item(Category, "category_id", "category")


def _get_item(model: Any, view_arg: str, name: str):
    """Shared resolver behind the four ``current_*`` proxies above.

    Looks up ``view_arg`` (e.g. ``"topic_id"``) in
    ``request.view_args``, queries `model` for it, and memoizes the
    result onto ``flask.g`` under `name` so a second access in the
    same request doesn't re-query the database.

    Two things worth knowing if you're debugging one of the
    ``current_*`` proxies:

    - If `view_arg` isn't in the current URL's view args (e.g. the
      route has no ``topic_id``), this returns whatever is already
      cached on `g` under `name` - ``None`` if nothing is, without
      ever touching the database.
    - ``g`` is request-scoped in a real request, but test suites that
      reuse a single Flask app/request context across many
      ``test_request_context()`` calls (as this project's fixtures
      do) can leak a stale cached value from one test into the next.
      See ``tests/unit/forum/test_forum_locals.py`` for how the
      Parte 1 tests work around this.
    """
    if (
        g
        and not getattr(g, name, None)
        and request.view_args
        and view_arg in request.view_args
    ):
        result = db.session.execute(
            select(model).filter_by(id=request.view_args[view_arg])
        ).scalar()
        setattr(g, name, result)
    return getattr(g, name, None)
