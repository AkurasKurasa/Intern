"""
Transformer-based Agent — Behavioral Cloning
=============================================
All logic lives in transformer.py.
"""

from .transformer import (
    TrajectoryDataset,
    TransformerAgentNetwork,
    PolicyOutput,
    train,
    predict,
    encode_state,
)

# Backward compat
TransformerPolicyNetwork = TransformerAgentNetwork

__all__ = [
    "TrajectoryDataset",
    "TransformerAgentNetwork",
    "TransformerPolicyNetwork",
    "PolicyOutput",
    "train",
    "predict",
    "encode_state",
]
