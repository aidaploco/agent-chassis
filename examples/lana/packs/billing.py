"""Billing capability — tools and instruction intent for a billing agent.
Mock-backed; a real billing system swaps these handlers only.

The mock is deliberately stateless: handlers never mutate _ACCOUNTS, so
conversations (and eval cases) can't contaminate each other through shared
module state — one cancelled test account would otherwise poison every
later run in the same worker process."""

import re

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

# The tool schema demands a reason string, so a model that wants to be helpful
# fills it in — typically by restating the request ("customer requested
# cancellation"). That is not a reason, and the instructions alone did not
# stop it (about half of "cancel dana@example.com" conversations cancelled).
# The handler is the backstop: a restatement or placeholder is refused, and
# the refusal tells the model what to do instead.
_NOT_A_REASON = re.compile(
    r"^\W*(?:(?:the\s+)?(?:customer|user|client|they)\s+)?"
    r"(?:requested|request|requests|wants?|wanted|asked|asks|would\s+like|"
    r"decided|decides|chose|choose)?\s*(?:to\s+)?"
    r"(?:cancel(?:l?ed|l?ing|lation)?|terminat\w*|end(?:ing)?|stop)\b"
    r"(?:\s+(?:the|their|my|this|his|her|a))?"
    r"(?:\s+(?:subscription|plan|account|service|it))?\W*$"
    r"|^\W*cancel\w*\s+(?:was\s+)?(?:requested|desired|wanted)\W*$"
    r"|^\W*$"
    r"|^\W*(?:n/?a|none|no\s+reason(?:\s+given)?|not\s+(?:given|provided|"
    r"specified|stated)|unknown|unspecified|unsure|-+)\W*$",
    re.IGNORECASE,
)
_NEEDS_REASON = (
    "Not cancelled: that is a restatement of the request, not a reason. Ask"
    " the customer what prompted the decision, wait for their answer, then"
    " call this again with their own words."
)


class BillingPack(CapabilityPack):
    name = "billing"
    description = "this customer's own account: subscription lookup, plan changes, invoices, cancellations (needs account email); not for what plans cost or include"

    def instructions(self, config: PackConfig) -> str:
        return (
            "You are the billing specialist. Resolve billing requests with your"
            " tools: subscriptions, plan changes, invoices, cancellations.\n"
            "Tool discipline: get the customer's email if you don't have it,"
            " then call the right tool immediately — don't preview what you're"
            " about to do. The one exception is cancellation, where you first"
            " decide WHAT they want cancelled:\n"
            "- An INVOICE ('cancel my invoices'): invoices are records and can't"
            " be cancelled. Don't ask why. Say so, then offer what you can do —"
            " show invoices, change the plan, or cancel the subscription — and"
            " ask which they mean. Once they give their email, continue from"
            " there (e.g. show the invoices) rather than starting over.\n"
            '- The SUBSCRIPTION or plan: your reply is the question "what'
            ' prompted the decision?" unless they have already told you why in'
            " this conversation. Do that BEFORE calling any tool for the"
            " cancellation, including lookups. NEVER call cancel_subscription"
            " until the customer has stated a reason; a restatement like"
            ' "customer requested cancellation" is not a reason, and you must'
            " never write one yourself.\n"
            "Not yours: general product questions: what a plan costs or"
            " includes, features and limits, SLA, security, company info, bugs,"
            " tickets. They belong to the support specialist, who has that"
            " knowledge. Route to support immediately; never answer that you"
            " lack the information.\n"
            "If a request about this customer's own account doesn't map to"
            " anything you can do, say what you can do and ask which they"
            " mean.\n"
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
            """Cancel a subscription; requires the customer's stated reason.

            Args:
                email: The account email.
                customer_stated_reason_verbatim: The customer's own words
                    saying WHY they are leaving (price, missing feature, a
                    competitor, ...), copied from what they wrote in this
                    conversation. "Customer requested cancellation" is not a
                    reason. If they have not said why, do not call this tool:
                    ask them first.
            """
            # Demo data, so this changes nothing and is safe to repeat. A real
            # cancellation runs at-least-once (see packs.idempotency_key) and must
            # pass that key to the billing system so a retry cannot cancel twice.
            if _NOT_A_REASON.match(customer_stated_reason_verbatim.strip()):
                return _NEEDS_REASON
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
