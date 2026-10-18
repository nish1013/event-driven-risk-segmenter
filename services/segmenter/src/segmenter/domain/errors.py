from uuid import UUID


class VersionConflict(RuntimeError):
    pass


class CheckNotOpen(RuntimeError):
    def __init__(self, check_id: UUID) -> None:
        super().__init__(f"check {check_id} is not open")
        self.check_id = check_id


class InvariantViolation(RuntimeError):
    pass
