"""Assistant-only loss mask (docs/harness/11 §2): loss on the answer tokens, never on the prompt."""

IGNORE = -100


def build_labels(
    prompt_ids: list[int], full_ids: list[int], *, max_len: int | None = None
) -> tuple[list[int], list[int], list[int]]:
    """Return ``(input_ids, labels, loss_mask)``; the prompt tokens are masked with -100.

    ``prompt_ids`` is the chat template up to and including the assistant header;
    ``full_ids`` additionally contains the assistant answer.
    """
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("prompt ids are not a prefix of the full ids (template mismatch)")
    if len(full_ids) <= len(prompt_ids):
        raise ValueError("no assistant target tokens after the prompt")
    ids = list(full_ids)
    if max_len is not None and len(ids) > max_len:
        ids = ids[:max_len]
        if len(ids) <= len(prompt_ids):
            raise ValueError("truncation would remove every target token")
    cut = len(prompt_ids)
    labels = [IGNORE] * cut + ids[cut:]
    mask = [0] * cut + [1] * (len(ids) - cut)
    return ids, labels, mask
