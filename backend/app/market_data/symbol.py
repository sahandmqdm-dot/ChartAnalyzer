from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Symbol:
    """Canonical exchange-independent market symbol."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not normalized or not normalized.isalnum():
            raise ValueError("symbol must contain only letters and numbers")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
