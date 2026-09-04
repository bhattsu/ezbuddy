"""Target geography for resolving configuration UUID foreign keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportGeography:
    """Where configuration.workflows attach in the operational/configuration graph."""

    state_code: str
    state_name: str
    county_name: str
    county_code: str | None
    jurisdiction_code: str
    jurisdiction_name: str
    jurisdiction_type: str = "DISTRICT_COURT"

    @staticmethod
    def texas_travis_default() -> ImportGeography:
        return ImportGeography(
            state_code="TX",
            state_name="Texas",
            county_name="Travis",
            county_code="TRAVIS",
            jurisdiction_code="TX-TRAVIS-DISTRICT",
            jurisdiction_name="Travis County District Court",
            jurisdiction_type="DISTRICT_COURT",
        )
