from typing import List, Union

Block = Union["EffectBlock", "DividerBlock"]  # For type hints


class EffectBlock:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"EffectBlock('{self.name}')"


class DividerBlock:
    def __init__(
        self,
        divider: str,
        branch_a: List[Block] = [],
        branch_b: List[Block] = [],
        mixer: str = "",
    ):
        self.divider_name = divider
        self.branch_a = branch_a
        self.branch_b = branch_b
        self.mixer_name = mixer

    def __repr__(self):
        return (
            f"DividerBlock(divider='{self.divider_name}', "
            f"branch_a={self.branch_a}, "
            f"branch_b={self.branch_b}, "
            f"mixer='{self.mixer_name}')"
        )


def parse_chain(tokens, stop_tokens=None) -> List[Block]:
    """
    Parse a series of tokens into a list of Blocks.
    stop_tokens is a set of tokens that signal we should return
    (e.g., 'BRANCHSPLIT1', 'MIXER1' for a specific divider).
    """
    if stop_tokens is None:
        stop_tokens = set()

    chain = []
    while tokens:
        # Peek at the next token without removing
        next_token = tokens[0]

        # If we've hit a stop token, return to the caller
        if next_token in stop_tokens:
            return chain

        # Otherwise, consume this token
        token = tokens.pop(0)

        # Check if it's a divider
        if token.startswith("DIVIDER"):
            # parse the divider fully, return a DividerBlock
            divider_block = parse_divider(tokens, token)
            chain.append(divider_block)

        # If it's not a divider, it's a normal effect block
        else:
            chain.append(EffectBlock(token))

    return chain


def parse_divider(tokens, divider_name: str) -> DividerBlock:
    """
    Parse a single divider block, collecting branch A and B.
    Example usage: if we see 'DIVIDER1', we call parse_divider(tokens, 'DIVIDER1').
    We'll read tokens into branch A until we see 'BRANCHSPLIT1', then read branch B
    until we see 'MIXER1'.
    """
    # The numeric part to help identify branchsplit/mixer tokens
    # (For simplicity, you might just store the entire 'DIVIDER1' or parse out '1')
    divider_id = divider_name.replace("DIVIDER", "")

    block = DividerBlock(divider_name)

    branchsplit_token = f"BRANCHSPLIT{divider_id}"
    mixer_token = f"MIXER{divider_id}"

    #
    # 1) Parse Branch A
    #
    # We read until we hit BRANCHSPLIT{id}, or mixer_token, or possibly another stop condition.
    stop_tokens_for_branch_a = {branchsplit_token, mixer_token}
    block.branch_a = parse_chain(tokens, stop_tokens_for_branch_a)

    # Next token must be either BRANCHSPLIT or MIXER
    if tokens:
        next_token = tokens.pop(0)
        if next_token.startswith("BRANCHSPLIT"):
            # We are indeed splitting to branch B
            # 2) Parse Branch B
            stop_tokens_for_branch_b = {mixer_token}
            block.branch_b = parse_chain(tokens, stop_tokens_for_branch_b)
            # Now the next token should be the mixer
            if tokens:
                mixer = tokens.pop(0)
                block.mixer_name = mixer
        else:
            # That means we hit the mixer directly (unusual, but the GT might allow an empty branch B)
            block.mixer_name = next_token
    else:
        # No tokens left? Possibly a malformed chain
        pass

    return block


def serialize_chain(blocks: List[Block]) -> List[str]:
    """
    Convert a list of blocks (possibly including nested dividers)
    back into the linear GT-1000 token list.
    """
    output = []
    for block in blocks:
        if isinstance(block, EffectBlock):
            output.append(block.name)
        elif isinstance(block, DividerBlock):
            # e.g. "DIVIDER2"
            output.append(block.divider_name)

            # Serialize branch A
            output.extend(serialize_chain(block.branch_a))

            # e.g. "DIVIDER2" -> id "2", so we want "BRANCHSPLIT2"
            divider_id = block.divider_name.replace("DIVIDER", "")
            branchsplit_token = f"BRANCHSPLIT{divider_id}"
            output.append(branchsplit_token)

            # Serialize branch B
            output.extend(serialize_chain(block.branch_b))

            # Finally, the mixer for this divider
            output.append(block.mixer_name)
    return output
