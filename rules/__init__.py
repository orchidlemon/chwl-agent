"""
Rules layer — pure business logic, no external I/O.
Each rule accepts partial context (fields can be None) and returns:
  - hard_blocks: absolute prohibitions
  - constraints: soft planning constraints
  - recommendations: preferred directions
  - missing_for_complete_check: fields the rule still needs
  - questions_to_ask_user: [{field, question, priority}]

LLM flow:
  1. Call rule tool with what it knows so far
  2. Rule returns questions_to_ask_user if data is incomplete
  3. LLM asks user, gets answers, re-calls rule with full data
  4. LLM uses hard_blocks + constraints to filter/guide tool calls
"""
from .age_policy import check_age_policy
from .group_policy import check_group_policy
from .time_policy import check_time_policy
from .itinerary_structure import check_itinerary_structure
from .food_policy import check_food_policy

__all__ = [
    "check_age_policy",
    "check_group_policy",
    "check_time_policy",
    "check_itinerary_structure",
    "check_food_policy",
]
