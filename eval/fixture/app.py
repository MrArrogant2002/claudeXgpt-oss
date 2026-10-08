"""Billing reconciliation, trimmed to the parts the agent is asked about."""


def reconcile(invoices, payments):
    """Match payments to invoices by reference. Amounts are minor units
    (integer paise/cents) so that totals never accumulate float error."""
    outstanding = {}
    for inv in invoices:
        outstanding[inv["ref"]] = inv["amount_minor"]
    for pay in payments:
        ref = pay["ref"]
        if ref in outstanding:
            outstanding[ref] -= pay["amount_minor"]
    return {ref: amt for ref, amt in outstanding.items() if amt != 0}
