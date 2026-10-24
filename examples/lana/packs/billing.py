"""Billing capability — tools and instruction intent for a billing agent.
Mock-backed; a real billing system swaps these handlers only.

The mock is deliberately stateless: handlers never mutate _ACCOUNTS, so
conversations (and eval cases) can't contaminate each other through shared
module state — one cancelled test account would otherwise poison every
later run in the same worker process."""

from agent_chassis.packs import CapabilityPack, PackConfig, ToolSpec

_ACCOUNTS: dict[str, dict] = {
    "dana@example.com": {
        "plan": "Pro",
        "seats": 10,
        "amount_usd": 199,
        "renewal_date": "August 1, 2026",
        "status": "Active",
        "invoices": [
            ("INV-2041", "June 1, 2026", 199, "Paid"),
            ("INV-2103", "July 1, 2026", 199, "Paid"),
        ],
    },
    "sam@example.org": {
        "plan": "Starter",
        "seats": 1,
        "amount_usd": 49,
        "renewal_date": "July 22, 2026",
        "status": "Active",
        "invoices": [("INV-1990", "June 22, 2026", 49, "Paid")],
    },
}
_VALID_PLANS = ("Starter", "Pro", "Enterprise")
_NO_ACCOUNT = "No account found for {email}. Please double-check the email address."


class BillingPack(CapabilityPack):
    name = "billing"
    description = "this customer's own account: subscription lookup, plan changes, invoices, cancellations (needs account email)"

    def instructions(self, config: PackConfig) -> str:
        return (
            "You are the billing specialist. Resolve billing requests with your"
            " tools: subscriptions, plan changes, invoices, cancellations.\n"
            "Tool discipline: get the customer's email if you don't have it,"
            " then call the right tool immediately — don't preview what you're"
            " about to do. Exception: if the customer wants to cancel but gave"
            " no reason, ask what prompted the decision first — NEVER call"
            " cancel_subscription until the customer has stated a reason in this"
            " conversation.\n"
            "If a request doesn't map to anything you can do (e.g. 'cancel an"
            " invoice' — invoices are records and can't be cancelled), say"
            " what you can do — show invoices, change the plan, or cancel the"
            " subscription — and ask which they mean.\n"
            "Quote plan names, amounts, and dates verbatim from tool output."
            " Lead with the answer. One issue at a time."
        )

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def get_subscription(email: str) -> str:
            """Look up a customer's subscription by account email."""
            account = _ACCOUNTS.get(email.lower())
            if account is None:
                return _NO_ACCOUNT.format(email=email)
            return (
                f"Plan: {account['plan']} | Seats: {account['seats']} |"
                f" ${account['amount_usd']}/month | Renews {account['renewal_date']}"
                f" | Status: {account['status']}"
            )

        async def get_invoice_history(email: str) -> str:
            """List a customer's recent invoices."""
            account = _ACCOUNTS.get(email.lower())
            if account is None:
                return _NO_ACCOUNT.format(email=email)
            return "\n".join(
                f"{i} — {d} — ${a} — {s}" for i, d, a, s in account["invoices"]
            )

        async def update_plan(email: str, new_plan: str) -> str:
            """Change a customer's plan (Starter, Pro, Enterprise)."""
            if new_plan not in _VALID_PLANS:
                return f"'{new_plan}' is not a valid plan. Available: {', '.join(_VALID_PLANS)}."
            account = _ACCOUNTS.get(email.lower())
            if account is None:
                return _NO_ACCOUNT.format(email=email)
            if account["plan"] == new_plan:
                return f"The account is already on the {new_plan} plan."
            return (
                f"Plan updated from {account['plan']} to {new_plan},"
                " effective immediately."
            )

        async def cancel_subscription(
            email: str, customer_stated_reason_verbatim: str
        ) -> str:
            """Cancel a subscription; requires the customer's stated reason."""
            account = _ACCOUNTS.get(email.lower())
            if account is None:
                return _NO_ACCOUNT.format(email=email)
            return (
                "Subscription cancelled; access continues until"
                f" {account['renewal_date']}. Recorded reason:"
                f" {customer_stated_reason_verbatim}"
            )

        return [
            ToolSpec("get_subscription", get_subscription, "Subscription lookup"),
            ToolSpec("get_invoice_history", get_invoice_history, "Invoice history"),
            ToolSpec("update_plan", update_plan, "Change plan"),
            ToolSpec(
                "cancel_subscription",
                cancel_subscription,
                "Cancel a subscription. Only call this after the customer has"
                " stated their own reason for cancelling in this conversation;"
                " pass that reason verbatim. If no reason has been given yet,"
                " do not call this — ask what prompted the decision instead."
                " Never invent or infer a reason.",
            ),
        ]


PACKS = [BillingPack()]
