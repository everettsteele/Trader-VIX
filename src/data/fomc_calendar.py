"""
Trader-VIX — FOMC Calendar
Static dataset of FOMC meeting announcement dates.
No positions opened within FOMC_BLACKOUT_DAYS of these dates.
All dates are publicly announced in advance — no lookahead bias.
Source: federalreserve.gov
"""

FOMC_DATES = [
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    # 2027
    "2027-01-27", "2027-03-17", "2027-05-05", "2027-06-16",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-15",
]


def is_fomc_blackout(date_str: str, blackout_days: int = 5) -> bool:
    from datetime import datetime
    target = datetime.strptime(date_str, "%Y-%m-%d")
    return any(
        abs((target - datetime.strptime(d, "%Y-%m-%d")).days) <= blackout_days
        for d in FOMC_DATES
    )


def days_to_next_fomc(date_str: str) -> int:
    from datetime import datetime
    target = datetime.strptime(date_str, "%Y-%m-%d")
    future = [datetime.strptime(d, "%Y-%m-%d") for d in FOMC_DATES if datetime.strptime(d, "%Y-%m-%d") > target]
    return (min(future) - target).days if future else 999
