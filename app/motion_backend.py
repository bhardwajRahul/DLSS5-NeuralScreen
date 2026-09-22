"""Track the backend actually initialized by the current worker."""


def normalize_backend(value):
    """The motion backend name: NVOFA is the default, anything else is CPU.

    Only the exact string "cpu" selects CPU DIS. Everything else - a missing
    key, an old config that predates the field, junk - means the shipped
    default, NVOFA (user rule 16.09). The worker path is what the program is
    built around; CPU stays as the automatic fallback when the driver
    refuses, and as an explicit choice in the menu.
    """
    return "cpu" if value == "cpu" else "nvofa"


class MotionBackendStatus:
    def __init__(self):
        self.worker = None
        self.active = False
        self.failed = False
        self._scanned = 0

    def update(self, worker, logs):
        if worker is not self.worker:
            self.worker = worker
            self.active = self.failed = False
        # Keep the last verdict when the bounded log buffer rotates.
        for line in reversed(logs):
            if "[nvofa] unavailable:" in line:
                self.active, self.failed = False, True
                break
            if "[nvofa] active:" in line:
                self.active, self.failed = True, False
                break
        return self.active
