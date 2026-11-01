"""频率安全：搜索与下载请求独立限速（最小间隔 + 滑动窗口 + 连续失败退避）。

需求文档 §下载与匹配：搜索与下载请求独立限速，设置最小间隔与滑动窗口上限，
连续失败时自动退避，防止 IP 封禁。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    min_interval_s: float = 1.2
    window_max: int = 10
    window_s: float = 60.0
    backoff_base_s: float = 5.0
    max_consecutive_failures: int = 3

    _last: float | None = field(default=None, init=False)
    _stamps: deque = field(default_factory=deque, init=False)
    _failures: int = field(default=0, init=False)
    _sleep = staticmethod(time.sleep)
    _clock = staticmethod(time.monotonic)

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def _window_gate(self) -> float:
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= self.window_s:
            self._stamps.popleft()
        if len(self._stamps) >= self.window_max:
            return self.window_s - (now - self._stamps[0])
        return 0.0

    def wait(self) -> float:
        """阻塞到允许发下一个请求；返回实际休眠秒数（便于测试与日志）。"""
        slept = 0.0
        now = self._clock()
        if self._last is not None:
            gap = self.min_interval_s - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                slept += gap
        window_gap = self._window_gate()
        if window_gap > 0:
            self._sleep(window_gap)
            slept += window_gap
        if self._failures:
            # 连续失败退避：5s, 10s, 20s ...
            delay = self.backoff_base_s * (2 ** max(0, self._failures - 1))
            self._sleep(delay)
            slept += delay
        now = self._clock()
        self._last = now
        self._stamps.append(now)
        return slept

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1

    @property
    def tripped(self) -> bool:
        """连续失败达到阈值：应停止本轮并转人工处理。"""
        return self._failures >= self.max_consecutive_failures
