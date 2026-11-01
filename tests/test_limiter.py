from nm.limiter import RateLimiter


def fake_limiter(min_interval_s=1.0, window_max=100, window_s=60.0):
    clock = {"now": 0.0}
    sleeps = []
    limiter = RateLimiter(min_interval_s=min_interval_s, window_max=window_max,
                          window_s=window_s, backoff_base_s=5.0, max_consecutive_failures=3)
    limiter._clock = lambda: clock["now"]

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    limiter._sleep = sleep
    return limiter, clock, sleeps


def test_min_interval_between_requests():
    limiter, clock, sleeps = fake_limiter(min_interval_s=1.0)
    limiter.wait()
    assert sleeps == []
    limiter.wait()
    assert sleeps == [1.0]


def test_sliding_window_cap():
    limiter, clock, sleeps = fake_limiter(min_interval_s=0, window_max=3, window_s=10.0)
    for _ in range(3):
        limiter.wait()
    assert len(limiter._stamps) == 3
    limiter.wait()  # 第 4 次必须等到窗口滑出
    assert sleeps and sleeps[-1] == 10.0


def test_backoff_grows_with_failures():
    limiter, clock, sleeps = fake_limiter(min_interval_s=0)
    limiter.wait()
    sleeps.clear()
    limiter.record_failure()
    limiter.wait()
    assert sleeps[-1] == 5.0          # 5 * 2^0
    sleeps.clear()
    limiter.record_failure()
    limiter.wait()
    assert sleeps[-1] == 10.0         # 5 * 2^1


def test_success_resets_failures():
    limiter, _, _ = fake_limiter(min_interval_s=0)
    limiter.record_failure()
    limiter.record_failure()
    limiter.record_success()
    assert limiter.consecutive_failures == 0
    assert not limiter.tripped


def test_trip_threshold():
    limiter, _, _ = fake_limiter(min_interval_s=0)
    for _ in range(3):
        limiter.record_failure()
    assert limiter.tripped
