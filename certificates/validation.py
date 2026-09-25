"""Problems found in an uploaded spreadsheet, described so they can be fixed.

Every issue says three things: **where** it is (a cell like ``B5``, or a row),
**what** is wrong, and **how** to fix it. That is the difference between an
upload that fails and an upload the user can actually correct.
"""

from dataclasses import dataclass

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One problem with the uploaded workbook."""

    level: str      # ERROR blocks the run; WARNING lets it through
    location: str   # "Cell B5", "Row 7", "Sheet 'Students'", "Workbook"
    problem: str    # what is wrong
    fix: str        # what to do about it

    @property
    def is_error(self):
        return self.level == ERROR

    def as_line(self):
        return f"{self.location}: {self.problem} Fix: {self.fix}"

    def __str__(self):
        return self.as_line()


def split(issues):
    """Return ``(errors, warnings)`` from a mixed list."""
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    return errors, warnings


class SpreadsheetError(ValueError):
    """Raised when the workbook cannot be used as it stands.

    ``issues`` carries the full, located list so the page can show every
    problem at once rather than making the user fix them one upload at a time.
    """

    def __init__(self, message, issues=None):
        super().__init__(message)
        self.message = message
        self.issues = list(issues or [])

    @property
    def errors(self):
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self):
        return [i for i in self.issues if not i.is_error]
