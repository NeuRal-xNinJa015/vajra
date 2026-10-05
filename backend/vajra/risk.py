"""Risk engine: a transparent risk level for each storm cell.

Risk = intensity now x likelihood over the alert period.

                     likelihood
    intensity     low      medium    high
    moderate      low      moderate  moderate
    strong        moderate high      high
    severe        moderate high      severe

Intensity is the cell's severity class (from peak reflectivity). Likelihood is
the highest storm or lightning probability along the cell's projected track
within the alert period: from the ML model where it could predict, otherwise
from the nowcast ensemble. The level and every fact behind it are returned, so
a forecaster can see why.
"""

from vajra.config import Settings
from vajra.contracts import RiskAssessment, StormCell

RISK_ORDER = ["low", "moderate", "high", "severe"]
_MATRIX = {
    "moderate": ("low", "moderate", "moderate"),
    "strong": ("moderate", "high", "high"),
    "severe": ("moderate", "high", "severe"),
}
# With no forecast for the cycle the likelihood is unknown; risk then reflects
# current intensity only, held one step below the top of that row.
_INTENSITY_ONLY = {"moderate": "low", "strong": "moderate", "severe": "high"}


def at_least(level: str, floor: str) -> bool:
    return RISK_ORDER.index(level) >= RISK_ORDER.index(floor)


def assess(cell: StormCell, projections: list[StormCell], settings: Settings) -> RiskAssessment:
    severity = cell.severity or "moderate"
    factors = []
    if cell.max_dbz is not None:
        factors.append(f"Peak reflectivity {cell.max_dbz:.1f} dBZ ({severity} intensity)")
    if cell.speed_kmh is not None:
        factors.append(f"Moving at {cell.speed_kmh:.0f} km/h")

    ahead = [p for p in projections if p.lead_time_min <= settings.alerts.valid_min]
    period = f"next {settings.alerts.valid_min} min"
    ml = [(p.ml_lightning_probability, "lightning") for p in ahead if p.ml_lightning_probability is not None]
    ml += [(p.ml_storm_probability, "storm") for p in ahead if p.ml_storm_probability is not None]
    ensemble = [p.probability for p in ahead if p.probability is not None]

    if ml:
        likelihood, source = max(value for value, _ in ml), "ml"
        for kind in ("lightning", "storm"):
            values = [value for value, name in ml if name == kind]
            if values:
                factors.append(f"{kind.capitalize()} probability {max(values):.0%} in the {period} (ML model)")
    elif ensemble:
        likelihood, source = max(ensemble), "ensemble"
        factors.append(f"Storm probability {likelihood:.0%} in the {period} (nowcast ensemble; ML not yet available)")
    else:
        factors.append("No forecast for this cycle: risk reflects current intensity only")
        return RiskAssessment(level=_INTENSITY_ONLY[severity], likelihood_source="none", factors=factors)

    spread = [p.uncertainty for p in ahead if p.uncertainty is not None]
    if spread:
        factors.append(f"Forecast spread up to ±{max(spread):.1f} dBZ")
    band = 2 if likelihood >= settings.risk.likelihood_high else 1 if likelihood >= settings.risk.likelihood_medium else 0
    return RiskAssessment(
        level=_MATRIX[severity][band], likelihood=likelihood, likelihood_source=source, factors=factors
    )
