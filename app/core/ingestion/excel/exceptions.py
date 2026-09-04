"""Excel parser errors."""


class ExcelParserError(Exception):
    """Base error for legal package Excel parsing."""


class MissingSheetError(ExcelParserError):
    def __init__(self, missing: set[str], extra: set[str] | None = None) -> None:
        self.missing = missing
        self.extra = extra or set()
        parts = [f"missing sheet(s): {sorted(missing)}"]
        if self.extra:
            parts.append(f"unexpected sheet(s): {sorted(self.extra)}")
        super().__init__("; ".join(parts))


class MissingColumnError(ExcelParserError):
    def __init__(self, sheet: str, missing: set[str]) -> None:
        self.sheet = sheet
        self.missing = missing
        super().__init__(f"sheet {sheet!r} missing column(s): {sorted(missing)}")


class EmptySheetError(ExcelParserError):
    def __init__(self, sheet: str, message: str) -> None:
        super().__init__(f"sheet {sheet!r}: {message}")


class PackageSheetError(ExcelParserError):
    """Invalid or missing Package metadata row."""
