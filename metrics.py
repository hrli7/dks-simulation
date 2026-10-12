"""Metric definitions for simulated shopping occasions."""

GROUP = {
    "DICK’S Sporting Goods",
    "Foot Locker",
    "Golf Galaxy",
    "Public Lands",
    "Going Going Gone",
    "Champs",
    "Kids Foot Locker",
}


def summarize(rows, banner=None):
    n = len(rows)
    entered = [
        r for r in rows if (banner in r["entered"] if banner else bool(set(r["entered"]) & GROUP))
    ]
    buyers = [
        r
        for r in rows
        if r["purchase"]
        and (r["purchase"]["retailer"] == banner if banner else r["purchase"]["retailer"] in GROUP)
    ]
    revenue = sum(r["purchase"]["revenue"] for r in buyers)
    return dict(
        occasions=n,
        unique_entrants=len(entered),
        entry_rate=len(entered) / n if n else None,
        buyers=len(buyers),
        conversion_per_entrant=len(buyers) / len(entered) if entered else None,
        purchase_rate=len(buyers) / n if n else None,
        revenue=revenue,
        revenue_per_occasion=revenue / n if n else None,
        average_ticket=revenue / len(buyers) if buyers else None,
        units_per_ticket=sum(len(r["purchase"]["items"]) for r in buyers) / len(buyers)
        if buyers
        else None,
        revenue_from_launch_only_items=sum(
            o["price"]
            for r in buyers
            for o in r["purchase"]["items"]
            if o["family"] == "launch_listing"
        ),
        revenue_from_unknown_variant_items=sum(
            o["price"]
            for r in buyers
            for o in r["purchase"]["items"]
            if o["size_evidence"] == "unknown"
        ),
        censored=sum(r["terminal"] == "step_cap_censored" for r in rows),
    )
