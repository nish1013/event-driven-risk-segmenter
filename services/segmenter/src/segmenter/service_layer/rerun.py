from collections.abc import Callable
from dataclasses import dataclass

from segmenter.domain.errors import VersionConflict

MAX_RUNS = 3
FIRST_BACKOFF = 0.05


@dataclass(frozen=True)
class Ran[T]:
    result: T
    runs: int


def rerun_on_conflict[T](use_case: Callable[[], T], sleep: Callable[[float], None]) -> Ran[T]:
    # A4: a version conflict reruns the whole use case, each wait longer than the last.
    run = 1
    while True:
        try:
            return Ran(use_case(), run)
        except VersionConflict:
            if run == MAX_RUNS:
                raise
            sleep(FIRST_BACKOFF * 2 ** (run - 1))
            run += 1
