from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IndicatorConfig:
    key: str
    label: str
    parquet_file: str
    indicator_code: str
    layout_url: str
    grid_template: str
    grid_variant_field: str | None = None
    chart_type: Literal["five_by_five"] = "five_by_five"
    exclude_reporting_years: tuple[int, ...] = ()


INDICATORS: dict[str, IndicatorConfig] = {
    "chronic": IndicatorConfig(
        key="chronic",
        label="Chronic Absenteeism",
        parquet_file="chronic.parquet",
        indicator_code="CHRO",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/chronic24.asp",
        grid_template="chronic",
    ),
    "ela": IndicatorConfig(
        key="ela",
        label="Academic ELA",
        parquet_file="ela.parquet",
        indicator_code="ELA",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/ela24.asp",
        grid_template="ela_38",
        grid_variant_field="hscutpoints",
    ),
    "math": IndicatorConfig(
        key="math",
        label="Academic Math",
        parquet_file="math.parquet",
        indicator_code="MATH",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/math24.asp",
        grid_template="math_38",
        grid_variant_field="hscutpoints",
    ),
    "elpi": IndicatorConfig(
        key="elpi",
        label="English Learner Progress (ELPI)",
        parquet_file="elpi.parquet",
        indicator_code="ELPI",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/elpi24.asp",
        grid_template="elpi",
    ),
    "susp": IndicatorConfig(
        key="susp",
        label="Suspension Rate",
        parquet_file="susp.parquet",
        indicator_code="SUSP",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/suspindicator24.asp",
        grid_template="susp_unified",
        grid_variant_field="type",
    ),
    "grad": IndicatorConfig(
        key="grad",
        label="Graduation Rate",
        parquet_file="grad.parquet",
        indicator_code="GRAD",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/gradindicator24.asp",
        grid_template="grad",
    ),
    "cci": IndicatorConfig(
        key="cci",
        label="College/Career Indicator (CCI)",
        parquet_file="cci.parquet",
        indicator_code="CCI",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/ccdata2024.asp",
        grid_template="cci",
    ),
    "science": IndicatorConfig(
        key="science",
        label="Science",
        parquet_file="science.parquet",
        indicator_code="SCIENCE",
        layout_url="https://www.cde.ca.gov/ta/ac/cm/fivebyfivecolortables.asp",
        grid_template="science",
        exclude_reporting_years=(2024,),
    ),
}


def get_indicator(key: str) -> IndicatorConfig:
    if key not in INDICATORS:
        raise KeyError(f"Unknown indicator: {key}")
    return INDICATORS[key]


def list_indicators() -> list[IndicatorConfig]:
    return list(INDICATORS.values())
