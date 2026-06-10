"""Pipeline configuration: which FRED series feed the dashboard.

Series IDs follow FRED's MSA naming convention. They are easy to mistype, so
the pull script (scripts/pull_fred.py) resolves each one against FRED's
metadata endpoint and records any that fail — check the run summary and fix
IDs here if a series comes back with an error.

Metro CBSA codes: Austin-Round Rock-Georgetown = 12420,
San Antonio-New Braunfels = 41700.
"""
from __future__ import annotations

DFW = "Dallas-Fort Worth"   # primary C&W market (MarketBeat default)
AUSTIN = "Austin"           # comparison metro (Economic Health)
SAN_ANTONIO = "San Antonio"  # comparison metro (Economic Health)
HOUSTON = "Houston"         # comparison metro (Economic Health)

# indicator key -> {market -> FRED series_id}
FRED_SERIES: dict[str, dict[str, str]] = {
    "employment_total_nonfarm": {
        AUSTIN: "AUST448NAN",       # All Employees: Total Nonfarm, Austin MSA
        SAN_ANTONIO: "SANA748NAN",  # All Employees: Total Nonfarm, San Antonio MSA
        DFW: "DALL148NAN",          # Dallas-Fort Worth MSA (peer)
        HOUSTON: "HOUS448NAN",      # Houston MSA (peer)
    },
    "unemployment_rate": {          # the 2nd MarketBeat economic indicator (metro unemployment)
        DFW: "DALL148URN",          # Unemployment Rate, Dallas-Fort Worth MSA
        AUSTIN: "AUST448URN",       # Unemployment Rate, Austin MSA
        SAN_ANTONIO: "SANA748URN",  # Unemployment Rate, San Antonio MSA
        HOUSTON: "HOUS448URN",      # Unemployment Rate, Houston MSA
    },
    "resident_population": {
        AUSTIN: "AUSPOP",           # Resident Population, Austin MSA (frozen → Austin uses county-sum)
        SAN_ANTONIO: "SATPOP",      # Resident Population, San Antonio MSA (current)
        DFW: "DFWPOP",              # Dallas-Fort Worth MSA (current)
    },
    "real_gdp_all_industry": {
        DFW: "RGMP19100",           # Real GDP: All Industry, Dallas-Fort Worth MSA
        AUSTIN: "RGMP12420",        # Real GDP: All Industry, Austin MSA
        SAN_ANTONIO: "RGMP41700",   # Real GDP: All Industry, San Antonio MSA
    },
    # --- CRE demand drivers (sector-aware: office vs industrial leasing) ---
    "employment_office_using": {    # Prof & Business Services — office demand
        DFW: "DALL148PBSV",
        AUSTIN: "AUST448PBSV",
        SAN_ANTONIO: "SANA748PBSV",
    },
    "employment_industrial": {      # Trade, Transportation & Utilities — logistics demand
        DFW: "DALL148TRADN",
        AUSTIN: "AUST448TRADN",
        SAN_ANTONIO: "SANA748TRAD",
    },
    "building_permits": {           # New private housing structures authorized — supply pipeline
        AUSTIN: "AUST448BPPRIV",
        SAN_ANTONIO: "SANA748BPPRIV",
    },
    "house_price_index": {          # All-transactions HPI — affordability / capital pressure
        AUSTIN: "ATNHPIUS12420Q",
        SAN_ANTONIO: "ATNHPIUS41700Q",
    },
    # --- demographics (Census data re-published by FRED — no Census key needed) ---
    "median_household_income": {    # principal-county proxy for the metro
        DFW: "MHITX48113A052NCEN",           # Dallas County, TX
        AUSTIN: "MHITX48453A052NCEN",       # Travis County, TX
        SAN_ANTONIO: "MHITX48029A052NCEN",  # Bexar County, TX
    },
    "real_per_capita_income": {     # real per-capita personal income, MSA
        DFW: "RPIPC19100",
        AUSTIN: "RPIPC12420",
        SAN_ANTONIO: "RPIPC41700",
    },
    "avg_weekly_wage": {            # QCEW total-covered, QUARTERLY — freshest income signal
        AUSTIN: "ENUC124240010SA",       # Austin MSA, seasonally adjusted
        SAN_ANTONIO: "ENUC417040010SA",  # San Antonio MSA
        # Dallas-Fort Worth MSA has no seasonally-adjusted QCEW weekly-wage series on FRED;
        # Dallas demographics use median household income + real per-capita income instead.
    },
}

# Current MSA population = sum of constituent counties (all current to 2025). We use
# the same method for both metros for consistency — and because some MSA-level series
# freeze after a redefinition (AUSPOP stopped at 2022).
FRED_MSA_POP_COUNTIES: dict[str, list[str]] = {
    AUSTIN: ["TXTRAV3POP", "TXWILL5POP", "TXHAYS9POP", "TXBAST1POP", "TXCALD5POP"],
    SAN_ANTONIO: ["TXBEXA9POP", "TXCOMA1POP", "TXGUAD7POP", "TXWILS3POP",
                  "TXATAS3POP", "TXBAND9POP", "TXKEND9POP", "TXMEDI5POP"],
    HOUSTON: ["TXHARR1POP", "TXFORT5POP", "TXMONT0POP", "TXGALV7POP",  # ~95% of MSA (HTNPOP frozen)
              "TXLIBE1POP", "TXWALL3POP", "TXCHAM1POP"],
}

# National context (single value per indicator, market labeled "US").
FRED_NATIONAL_SERIES: dict[str, str] = {
    "cpi_all_urban": "CPIAUCSL",     # CPI-U: All Items, US
    "gdp": "GDP",                    # Gross Domestic Product, US
    "unemployment_rate_us": "UNRATE",  # Civilian Unemployment Rate, US
    "employment_total_nonfarm_us": "PAYEMS",  # US total nonfarm — job-growth benchmark
    "population_us": "POPTHM",        # US population (monthly, thousands) — pop-growth benchmark
}

# Default years of history to request.
DEFAULT_HISTORY_YEARS = 10
