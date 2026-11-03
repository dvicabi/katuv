"""Bounded acoustic searches; recognizer text is used only to locate source tokens."""
import math
import unicodedata


def key(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text).casefold() if c.isalnum())


def matching_pairs(source, heard):
    """Global edit alignment, not greedy longest-block matching of repeated choruses.

    Substitutions and omissions both contribute to the whole-recording cost. The
    backtrace returns only exact lexical matches; ASR never supplies source text.
    """
    width = len(heard) + 1
    trace = [bytearray(width) for _ in range(len(source) + 1)]
    previous = list(range(width))
    for i, token in enumerate(source, 1):
        current = [i] + [0] * len(heard)
        for j, other in enumerate(heard, 1):
            diagonal = previous[j-1] + (token != other)
            deletion, insertion = previous[j] + 1, current[j-1] + 1
            cost = min(diagonal, deletion, insertion)
            current[j] = cost
            trace[i][j] = 0 if diagonal == cost else 1 if deletion == cost else 2
        previous = current
    pairs, i, j = [], len(source), len(heard)
    while i and j:
        direction = trace[i][j]
        if direction == 0:
            if source[i-1] and source[i-1] == heard[j-1]:
                pairs.append((i-1,j-1))
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def alignment_windows(tokens, guidance, duration, breaks=()):
    """Return source-index windows. Never manufacture a time for an unsupported block.

    Consecutive lexical matches provide context for repeated words. Sparse isolated
    matches are not enough to move a whole verse. Windows remain in source order.
    """
    heard = []
    for item in guidance:
        start, end = item.get('raw_start', item.get('start')), item.get('raw_end', item.get('end'))
        if (all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end))
                and 0 <= start < end <= duration + .001):
            heard.append(dict(item, start=start, end=end))
    matches = matching_pairs([key(t) for t in tokens], [key(w['text']) for w in heard])
    anchors = {}
    pairs = set(matches)
    for index, position in matches:
        context = any(0 < abs(index-i) <= 10 and 0 < abs(position-j) <= 10
                      and ((i-1,j-1) in pairs or (i+1,j+1) in pairs) for i,j in matches)
        if not context and len(tokens) > 1:
            continue
        word = heard[position]
        if (word.get('probability') or 0) >= .45:
            anchors[index] = word
    windows, lo = [], 0
    while lo < len(tokens):
        hi = min(len(tokens), lo + 10)
        hi = min([hi] + [i for i in breaks if lo < i < hi])
        # Break before a long pause instead of forcing across music/interludes.
        for i in range(lo + 1, hi):
            previous = [j for j in anchors if lo <= j < i]
            if i in anchors and previous and anchors[i]['start'] - anchors[previous[-1]]['end'] > 2:
                hi = i
                break
        inside = [i for i in anchors if lo <= i < hi]
        before = [i for i in anchors if i < lo]
        after = [i for i in anchors if i >= hi]
        start = max(0., anchors[before[-1]]['end'] - .3) if before else 0.
        end = min(duration, anchors[after[0]]['start'] + .3) if after else duration
        if inside:
            start = max(start, anchors[inside[0]]['start'] - 1 - (inside[0] - lo) * 2)
        if inside and end - start > 30:
            # Bound memory and avoid a distant repeated chorus. Keep room for
            # sustained singing; character-count duration guesses cut it short.
            start = max(start, anchors[inside[0]]['start'] - .8 - (inside[0] - lo) * 2)
            end = min(end, start + 30)
        if before and not after:
            end = min(end, start + 30)
        if (inside or before) and .02 < end - start <= 30:
            windows.append(dict(first=lo, stop=hi, start=start, end=end))
        elif duration <= 30 and lo == 0 and hi == len(tokens):
            windows.append(dict(first=lo, stop=hi, start=0., end=duration))
        lo = hi
    # Shared context removes independently stretched word boundaries between lines.
    merged = []
    for window in windows:
        if (merged and merged[-1]['stop'] == window['first'] and window['start'] <= merged[-1]['end']
                and max(window['end'], merged[-1]['end'])-merged[-1]['start'] <= 30
                and window['stop']-merged[-1]['first'] <= 24):
            merged[-1].update(stop=window['stop'], end=max(window['end'], merged[-1]['end']))
        else:
            merged.append(dict(window))
    return merged
