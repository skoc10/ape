"""Deterministic rule engine: every finding in a report comes from here.

The LLM never adds findings; it only explains and prioritises what these rules found.
"""

from ape.rules.engine import run_rules
from ape.rules.model import RULES, Finding, ResourceRef, RuleInfo, fingerprint

__all__ = ["RULES", "Finding", "ResourceRef", "RuleInfo", "fingerprint", "run_rules"]
