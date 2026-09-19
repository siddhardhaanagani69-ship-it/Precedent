"""One weighted, priority-ordered capacity limit for all worker LLM calls."""

from contextlib import contextmanager
from itertools import count
from threading import Condition


class Scheduler:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.used = 0
        self.condition = Condition()
        self.waiters = []
        self.sequence = count()

    @contextmanager
    def slot(self, weight: int, lane: str):
        if not 1 <= weight <= self.capacity:
            raise ValueError("Model weight exceeds configured concurrency capacity")
        ticket = ({"debate": 0, "verify": 1, "bulk": 2}[lane], next(self.sequence))
        with self.condition:
            self.waiters.append(ticket)
            try:
                self.condition.wait_for(lambda: ticket == min(self.waiters) and self.used + weight <= self.capacity)
                self.used += weight
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()
        try:
            yield
        finally:
            with self.condition:
                self.used -= weight
                self.condition.notify_all()
