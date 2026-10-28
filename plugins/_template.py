"""Copy this file to plugins/my_skill.py (no leading underscore) and restart Jarvis.

Files starting with "_" are skipped, so this template never loads by itself.
Each @registry.tool function becomes a tool the brain can call. Keep descriptions short and
concrete - the model reads them to decide when to use the tool.
"""
from jarvis.tools import P, registry
from jarvis.tools import outcome


@registry.tool(
    "coin_flip",
    "Flip a coin (or roll a die with sides > 2) when the user wants a random decision.",
    {"sides": P("integer", "Number of sides (default 2 = coin)", required=False)},
)
def coin_flip(sides: int = 2) -> str:
    import random
    if sides <= 2:
        return outcome.done(f"It landed {random.choice(['heads', 'tails'])}.")
    return outcome.done(f"Rolled a {random.randint(1, sides)} on a {sides}-sided die.")


# Tools that change things can ask the user first - return a question to require a yes:
#
# @registry.tool("empty_recycle_bin", "Empty the Recycle Bin.", {},
#                confirm=lambda args: "Empty the Recycle Bin? This can't be undone.")
# def empty_recycle_bin() -> str: ...
