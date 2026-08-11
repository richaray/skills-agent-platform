"""
Starter data.

Runs once on an empty database so the deployed app is never a blank screen.
It creates:
  * documents and records for the read-only tools to work against
  * one demo skill with TWO published versions, so "compare versions" and
    "rerun an older version" are usable immediately

Everything here is invented sample data. It is safe to delete the database and
start again.
"""

from sqlalchemy.orm import Session

from app import models
from app.logging_setup import app_log, log_event

DOCUMENTS = [
    (
        "Refund Policy",
        "Customers may request a refund within 30 days of delivery. "
        "Orders delivered more than 30 days ago are not eligible. "
        "Gold tier customers receive 100% of the order value. "
        "Standard tier customers receive 90% of the order value. "
        "Any refund above 10000 INR must be escalated to the support lead "
        "before it is paid out.",
    ),
    (
        "Escalation Guidelines",
        "Escalate by creating a task assigned to 'support-lead' whenever a "
        "refund exceeds 10000 INR, whenever a customer has requested more than "
        "three refunds in a year, or whenever the order status is 'disputed'. "
        "The task must state the order id and the calculated amount.",
    ),
    (
        "Shipping Policy",
        "Standard delivery takes 3-7 working days. Express delivery takes 1-2 "
        "working days. A delivery is considered late if it arrives more than 2 "
        "days after the promised date. Late deliveries qualify for a shipping "
        "fee waiver but not an automatic product refund.",
    ),
]

RECORDS = [
    ("customer", "CUST-001", {"name": "Priya Nair", "tier": "gold", "orders_placed": 14}),
    ("customer", "CUST-002", {"name": "Arjun Mehta", "tier": "standard", "orders_placed": 3}),
    (
        "order",
        "ORD-1002",
        {
            "customer_id": "CUST-001",
            "item": "Wireless Headphones",
            "amount": 4500,
            "currency": "INR",
            "status": "delivered",
            "delivered_days_ago": 9,
        },
    ),
    (
        "order",
        "ORD-1003",
        {
            "customer_id": "CUST-002",
            "item": "Standing Desk",
            "amount": 18000,
            "currency": "INR",
            "status": "delivered",
            "delivered_days_ago": 12,
        },
    ),
    (
        "order",
        "ORD-1004",
        {
            "customer_id": "CUST-002",
            "item": "Monitor Arm",
            "amount": 3200,
            "currency": "INR",
            "status": "delivered",
            "delivered_days_ago": 61,
        },
    ),
    ("invoice", "INV-77", {"order_id": "ORD-1002", "paid": True, "amount": 4500}),
]

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order the customer is asking about, e.g. ORD-1002.",
        },
        "customer_message": {
            "type": "string",
            "description": "What the customer said, in their own words.",
        },
    },
    "required": ["order_id", "customer_message"],
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "eligible": {"type": "boolean", "description": "Is a refund allowed?"},
        "refund_amount": {"type": "number", "description": "Amount in INR. Use 0 if not eligible."},
        "reasoning": {"type": "string", "description": "Why, referring to the policy."},
        "policy_reference": {"type": "string", "description": "Which document supports this."},
        "escalated": {"type": "boolean", "description": "Was this escalated to a human?"},
    },
    "required": ["eligible", "refund_amount", "reasoning"],
}

V1_INSTRUCTIONS = """\
You assess whether a customer is entitled to a refund.

Steps to follow:
1. Look up the order with record_lookup (record_type='order') to get its amount,
   status and how long ago it was delivered.
2. Look up the customer with record_lookup (record_type='customer') to find their tier.
3. Search the document library for the refund policy.
4. Work out the refund amount with the calculator. Do not do arithmetic yourself.
5. Return your decision.

If the order does not exist, say so in your reasoning and mark it not eligible.
Set "escalated" to false - this version cannot escalate.\
"""

V2_INSTRUCTIONS = """\
You assess whether a customer is entitled to a refund, and escalate when the
policy says you must.

Steps to follow:
1. Look up the order with record_lookup (record_type='order') to get its amount,
   status and how long ago it was delivered.
2. Look up the customer with record_lookup (record_type='customer') to find their tier.
3. Search the document library for the refund policy AND the escalation guidelines.
4. Work out the refund amount with the calculator. Do not do arithmetic yourself.
5. If the escalation guidelines apply, use create_task to raise a task for
   'support-lead'. Include the order id and the calculated amount in the task.
   A human must approve this before it happens.
6. Return your decision, setting "escalated" to true only if a task was actually
   created and confirmed by the tool result.

If the order does not exist, say so in your reasoning and mark it not eligible.
Never claim a task was created unless the tool told you it was.\
"""

EXAMPLE = {
    "input": {
        "order_id": "ORD-1002",
        "customer_message": "The headphones stopped working after a week. I want my money back.",
    },
    "output": {
        "eligible": True,
        "refund_amount": 4500,
        "reasoning": "Delivered 9 days ago, within the 30 day window. Gold tier customer, so 100% of 4500.",
        "policy_reference": "Refund Policy",
        "escalated": False,
    },
}


def seed_if_empty(db: Session) -> None:
    """Adds the starter data, but only to a database that has none."""
    if db.query(models.Skill).count() > 0:
        return

    for title, content in DOCUMENTS:
        db.add(models.Document(title=title, content=content))

    for record_type, external_id, data in RECORDS:
        db.add(models.Record(record_type=record_type, external_id=external_id, data=data))

    skill = models.Skill(
        name="Refund Eligibility Assessor",
        purpose=(
            "Decides whether a customer qualifies for a refund, calculates the "
            "amount from policy, and escalates to a human when the policy requires it."
        ),
    )
    db.add(skill)
    db.flush()

    # Version 1: read-only. No write tool, so it can never escalate.
    db.add(
        models.SkillVersion(
            skill_id=skill.id,
            version_number=1,
            status="published",
            published_at=models.utcnow(),
            instructions=V1_INSTRUCTIONS,
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
            examples=[EXAMPLE],
            allowed_tools=["record_lookup", "document_search", "calculator"],
            approval_required_tools=[],
            max_steps=8,
        )
    )

    # Version 2: adds the write tool, which must require approval.
    db.add(
        models.SkillVersion(
            skill_id=skill.id,
            version_number=2,
            status="published",
            published_at=models.utcnow(),
            instructions=V2_INSTRUCTIONS,
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
            examples=[EXAMPLE],
            allowed_tools=["record_lookup", "document_search", "calculator", "create_task"],
            approval_required_tools=["create_task"],
            max_steps=10,
        )
    )

    db.commit()
    log_event(app_log, "seed_completed", skill_id=skill.id, documents=len(DOCUMENTS), records=len(RECORDS))
