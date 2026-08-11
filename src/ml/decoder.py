"""Maximum-likelihood sequence decoder for the LORAL IR link.

The measurements decided this design. Symbol classes are separated by 12-40
pooled standard deviations at every rate tested, so per-symbol classification
is already solved and a better classifier buys nothing. What actually fails is
capture: 40-60% of transmissions are missing at least one pulse, and a symbol
that was never observed cannot be recovered by any model that looks at pulses
one at a time.

So the decoding work is structural, not discriminative:

  Stage 1  emission model      P(observation | symbol), learned per rate
  Stage 2  constrained Viterbi protocol framing + skip transitions + ASCII

Three sources of evidence are combined:

  * Pulse width, via the emission model. Nearly decisive on its own.
  * The GAP preceding each pulse. A dropped symbol lengthens the following gap
    by its own width plus one inter-symbol gap, so the gap says both THAT a
    symbol was lost and WHICH one. Measured on hardware, 100% of single-drop
    passes carried this trace.
  * Protocol structure. Exactly eight data bits follow each START, and every
    payload byte is printable ASCII - so most ways of "repairing" a short byte
    are inadmissible, and the survivor is usually unique.

The state is (bit position, partial byte value), 256 states in total, which
lets the ASCII constraint prune during decoding rather than after it.
"""
import math
from collections import defaultdict

# Nominal symbol widths at rate 1.0 (microseconds), from src/beacon/beacon.py.
W = {"ZERO": 10_000, "ONE": 25_000, "START": 50_000}
GAP_US = 10_000
SYMBOLS = ("ZERO", "ONE", "START")

# Payload bytes are printable ASCII; the beacon appends \n as its terminator.
VALID_BYTES = frozenset(range(0x20, 0x7F)) | {0x0A}

MAX_SKIP = 3          # consecutive dropped symbols the decoder will consider
NEG_INF = float("-inf")

# Paths whose log-likelihood trails the leader by more than this are pruned.
# Verified lossless against no pruning at every rate; 80 was NOT - it cost
# 14 points of pass rate. Gives a 10-18x speedup over an exhaustive lattice.
BEAM = 400.0

_MACHINE_CACHE = {}


# --------------------------------------------------------------------------
# Stage 1: emission model
# --------------------------------------------------------------------------

def fit_emissions(passes, rate):
    """Gaussian class-conditional over pulse width, fitted per symbol.

    Generative rather than discriminative on purpose: Viterbi needs
    P(observation | symbol), and a Gaussian yields that directly instead of
    requiring a posterior to be divided back through the class priors.

    Only passes that captured every symbol can be labelled positionally, which
    is what makes these labels exact - no alignment heuristic is involved, so
    the emission model cannot inherit an alignment bug.
    """
    acc = defaultdict(list)
    for p in passes:
        if len(p["pulses"]) != p["n_expected"]:
            continue
        for sym, obs in zip(p["expected_symbols"], p["pulses"]):
            acc[sym].append(obs["dur"])

    model = {}
    for sym in SYMBOLS:
        vals = acc.get(sym)
        if vals and len(vals) > 1:
            mu = sum(vals) / len(vals)
            var = sum((v - mu) ** 2 for v in vals) / len(vals)
        else:
            mu, var = W[sym] / rate, (W[sym] / rate * 0.05) ** 2
        # Floor the variance: a class measured as near-deterministic would
        # otherwise dominate every path and make the decoder brittle.
        model[sym] = (mu, max(var, (0.02 * mu) ** 2))
    return model


def fit_gap_sigma(passes, rate):
    """Spread of the inter-symbol gap, from fully captured passes only."""
    nominal = GAP_US / rate
    devs = []
    for p in passes:
        if len(p["pulses"]) != p["n_expected"]:
            continue
        for obs in p["pulses"]:
            g = obs.get("gap", -1)
            if g and 0 < g < 5 * nominal:
                devs.append(g - nominal)
    if len(devs) < 2:
        return 0.15 * nominal
    mu = sum(devs) / len(devs)
    var = sum((d - mu) ** 2 for d in devs) / len(devs)
    return max(math.sqrt(var), 0.02 * nominal)


def log_gauss(x, mu, var):
    return -0.5 * (math.log(2 * math.pi * var) + (x - mu) ** 2 / var)


def fit_skip_prior(passes):
    """log P(a symbol is dropped), from the training passes' capture shortfall.

    Previously hardcoded at -6.0, which happened to sit near the rate-2 value
    but was badly wrong elsewhere - at rate 1 the link drops nothing at all, so
    a skip should be far more expensive than -6 makes it.

    Laplace-smoothed because a rate with zero observed drops would otherwise
    give log(0): the decoder must still be *able* to posit a skip, just very
    reluctantly.
    """
    expected = sum(p["n_expected"] for p in passes)
    missing = sum(max(0, p["n_expected"] - len(p["pulses"])) for p in passes)
    if expected <= 0:
        return -6.0
    return math.log((missing + 0.5) / (expected + 1.0))


# --------------------------------------------------------------------------
# Stage 2: protocol state machine
# --------------------------------------------------------------------------

def build_machine():
    """States and legal symbol transitions for the byte framing.

    A state is either "expect START", or "expecting data bit i with these
    bits already accumulated". Carrying the partial byte in the state is what
    allows the ASCII constraint to prune mid-byte instead of rejecting a
    completed byte after the fact.
    """
    states = [("S", 0)]
    index = {("S", 0): 0}
    for phase in range(1, 9):                    # expecting data bit phase-1
        for partial in range(1 << (phase - 1)):
            index[(phase, partial)] = len(states)
            states.append((phase, partial))

    trans = [[] for _ in states]                 # (symbol, next_state, byte|None)
    for i, (phase, partial) in enumerate(states):
        if phase == "S":
            trans[i].append(("START", index[(1, 0)], None))
            continue
        for bit, sym in ((0, "ZERO"), (1, "ONE")):
            val = partial | (bit << (phase - 1))
            if phase < 8:
                trans[i].append((sym, index[(phase + 1, val)], None))
            elif val in VALID_BYTES:             # ASCII prune at byte close
                trans[i].append((sym, index[("S", 0)], val))
    return states, index, trans


def build_skip_paths(trans, n_states, max_skip):
    """For each state, every way of advancing 0..max_skip symbols unobserved.

    Returns state -> [(end_state, skipped_symbols, bytes_completed), ...].
    Precomputed once: the Viterbi inner loop would otherwise re-walk this graph
    for every observation.
    """
    paths = []
    for s in range(n_states):
        found = [(s, (), ())]
        frontier = [(s, (), ())]
        for _ in range(max_skip):
            nxt = []
            for cur, syms, bytes_ in frontier:
                for sym, ns, byte in trans[cur]:
                    entry = (ns, syms + (sym,),
                             bytes_ + ((byte,) if byte is not None else ()))
                    nxt.append(entry)
            found.extend(nxt)
            frontier = nxt
        paths.append(found)
    return paths


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

def get_machine(max_skip):
    """Machine and skip-path table, built once per max_skip and reused.

    These are pure functions of the protocol, not of the data, but decode()
    used to rebuild all 256 states and their skip paths on every call - once
    per pass, hundreds of times per evaluation.
    """
    if max_skip not in _MACHINE_CACHE:
        states, index, trans = build_machine()
        _MACHINE_CACHE[max_skip] = (
            states, index, trans,
            build_skip_paths(trans, len(states), max_skip))
    return _MACHINE_CACHE[max_skip]


def decode(pulses, emissions, gap_sigma, rate, skip_log_prior=-6.0,
           max_skip=None):
    """Viterbi over the pulse sequence. Returns (symbols, bytes).

    skip_log_prior is the log-probability charged per unobserved symbol; see
    fit_skip_prior. It must be a real cost, or the decoder can invent
    arbitrarily many phantom symbols to force any byte into the ASCII range.
    """
    max_skip = MAX_SKIP if max_skip is None else max_skip
    states, index, trans, skip_paths = get_machine(max_skip)
    n_states = len(states)
    nominal_gap = GAP_US / rate

    best = [NEG_INF] * n_states
    best[0] = 0.0
    back = []

    # Largest gap the skip model can legitimately explain: MAX_SKIP missing
    # symbols, each the widest, plus the gaps around them.
    max_explainable = (max_skip + 1) * nominal_gap + max_skip * W["START"] / rate

    for t, obs in enumerate(pulses):
        dur = obs["dur"]
        gap = obs.get("gap", -1)

        # The first pulse of a pass is preceded by the beacon's 500ms
        # inter-message rest, since that rest is exactly what passes are split
        # on. It is not an inter-symbol gap, and treating it as one made the
        # decoder insert phantom symbols trying to account for half a second -
        # which then knocked the byte framing out of alignment for the rest of
        # the pass. The bound catches any other out-of-model gap.
        use_gap = (t > 0 and gap is not None
                   and 0 < gap < 1.5 * max_explainable)

        em = {sym: log_gauss(dur, *emissions[sym]) for sym in SYMBOLS}

        nxt = [NEG_INF] * n_states
        bp = {}
        cutoff = max(best) - BEAM
        for s in range(n_states):
            base = best[s]
            if base == NEG_INF or base < cutoff:
                continue
            for mid, skipped, done_bytes in skip_paths[s]:
                k = len(skipped)
                cost = base + skip_log_prior * k
                if use_gap:
                    # A dropped symbol shows up as a longer gap: one extra
                    # inter-symbol gap plus the missing pulse's own width.
                    expect = (k + 1) * nominal_gap + sum(W[x] / rate for x in skipped)
                    cost += log_gauss(gap, expect, (gap_sigma * (1 + k)) ** 2)
                for sym, ns, byte in trans[mid]:
                    c = cost + em[sym]
                    if c > nxt[ns]:
                        nxt[ns] = c
                        bp[ns] = (s, skipped, done_bytes, sym, byte)
        best = nxt
        back.append(bp)
        if all(v == NEG_INF for v in best):
            return None, None            # no admissible path

    end = max(range(n_states), key=lambda i: best[i])
    if best[end] == NEG_INF:
        return None, None

    syms, out_bytes, cur = [], [], end
    for t in range(len(pulses) - 1, -1, -1):
        if cur not in back[t]:
            return None, None
        prev, skipped, done, sym, byte = back[t][cur]
        syms.extend(reversed(list(skipped) + [sym]))
        if byte is not None:
            out_bytes.append(byte)
        out_bytes.extend(reversed(done))
        cur = prev
    syms.reverse()
    out_bytes.reverse()
    return syms, out_bytes
