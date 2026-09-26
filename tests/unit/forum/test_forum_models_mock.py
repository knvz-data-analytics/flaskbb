"""New mock-based unit test for flaskbb.forum.models (Tarefa 1.5 - Parte 1).

Post.save() reads the real system clock through time_utcnow() to stamp
date_created/last_updated. This test replaces that dependency with a
double (unittest.mock, via pytest-mock's `mocker` fixture) so the
assertions are deterministic and so we can verify the *interaction*
with the clock (how many times it is consulted, and with what final
effect), not just the resulting timestamp.
"""

import datetime

from flaskbb.forum.models import Post


class TestPostSaveUsesInjectedClock:
    def test_new_post_save_reads_the_clock_twice(self, database, topic, user, mocker):
        """Why the double was necessary: without mocking the clock,
        the only way to assert on `date_created` would be comparing
        it against `datetime.now()` with some tolerance window, which
        is flaky under CI load. Mocking `time_utcnow()` lets us pin an
        exact expected value and, more importantly, verify how many
        times Post.save() actually consults the clock, instead of
        only checking the final timestamp."""
        fixed_now = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        mocked_clock = mocker.patch(
            "flaskbb.forum.models.time_utcnow", return_value=fixed_now
        )

        post = Post(content="Mocked clock post")
        post.save(user=user, topic=topic)

        # Post.__init__ stamps date_created once, and Post.save()
        # reads the clock again to compute `created` (overwriting the
        # __init__ value) -- so the double records 2 calls, not 1.
        # Asserting the exact count is what caught this redundant
        # clock read; asserting only the final value would have
        # missed it.
        assert mocked_clock.call_count == 2
        assert post.date_created == fixed_now
