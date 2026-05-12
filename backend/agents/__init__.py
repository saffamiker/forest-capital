"""
agents/__init__.py

Agent registry for the Forest Capital Portfolio Intelligence System.
Exposes each specialist agent plus the CIO orchestrator and support agents.
"""
from agents.equity_analyst import EquityAnalyst
from agents.fixed_income_analyst import FixedIncomeAnalyst
from agents.risk_manager import RiskManager
from agents.quant_backtester import QuantBacktester
from agents.independent_analyst import IndependentAnalyst
from agents.cio import CIO
from agents.qa_agent import QAAgent
from agents.explainer_agent import ExplainerAgent

__all__ = [
    "EquityAnalyst",
    "FixedIncomeAnalyst",
    "RiskManager",
    "QuantBacktester",
    "IndependentAnalyst",
    "CIO",
    "QAAgent",
    "ExplainerAgent",
]
