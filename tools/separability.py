import csv, statistics as st
from collections import defaultdict

rows = list(csv.DictReader(open('training_data.csv')))
for r in rows:
    for k in ('round_index', 'duration_us', 'peak_amplitude', 'trough_amplitude',
              'baseline_amplitude', 'peak_hpf', 'edge_threshold', 'backoffs',
              'swing_from_baseline', 'swing_peak_to_trough'):
        r[k] = int(r[k])
    r['d'] = float(r['target_distance_in'])

print(f"{len(rows)} rows, rounds {sorted({r['round_index'] for r in rows})}, "
      f"distances {sorted({r['d'] for r in rows})}")
counts = defaultdict(int)
for r in rows:
    counts[(r['d'], r['round_index'])] += 1
print("rows per (distance, round):",
      dict(sorted({d: [counts[(d, k)] for k in sorted({r['round_index'] for r in rows})]
                   for d in sorted({r['d'] for r in rows})}.items())))

FEATS = [('peak_hpf', 'peak_hpf'), ('swing_from_baseline', 'swing'),
         ('swing_peak_to_trough', 'pk-pk')]

for key, label in FEATS:
    g = defaultdict(list)
    for r in rows:
        g[r['d']].append(r[key])
    ds = sorted(g)
    print(f"\n=== {label} ===")
    print(f"{'dist':>5} {'n':>4} {'mean':>9} {'sd':>8} {'cv':>7}   {'1/d^2 pred':>10} "
          f"{'sep from prev':>14}")
    ref_mean = st.mean(g[ds[0]])
    prev = None
    for d in ds:
        v = g[d]
        m, sd = st.mean(v), st.pstdev(v)
        pred = ref_mean * (ds[0] / d) ** 2
        line = f"{d:5.1f} {len(v):4d} {m:9.1f} {sd:8.1f} {100*sd/m:6.1f}% {pred:11.1f}"
        if prev:
            pm, psd = prev
            pooled = (sd + psd) / 2
            line += f"   {abs(m-pm)/pooled:11.1f} sd" if pooled else "        n/a"
        print(line)
        prev = (m, sd)

# ---- honest model evaluation: hold out an entire ROUND ----
try:
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
except ImportError:
    raise SystemExit("\n(sklearn not available)")

y = np.array([r['d'] for r in rows])
grp = np.array([r['round_index'] for r in rows])
featsets = {
    'peak_hpf only': ['peak_hpf'],
    'swing only': ['swing_from_baseline'],
    'pk-pk only': ['swing_peak_to_trough'],
    'peak_hpf + pk-pk': ['peak_hpf', 'swing_peak_to_trough'],
    'all amplitude': ['peak_hpf', 'swing_from_baseline', 'swing_peak_to_trough',
                      'peak_amplitude', 'baseline_amplitude', 'trough_amplitude'],
    'all + duration': ['peak_hpf', 'swing_from_baseline', 'swing_peak_to_trough',
                       'peak_amplitude', 'baseline_amplitude', 'trough_amplitude',
                       'duration_us'],
}

print("\n\n=== leave-one-ROUND-out accuracy (generalising across repositionings) ===")
print("A random split would leak: consecutive rows share a beacon placement.\n")
print(f"{'features':<20} {'RandomForest':>13} {'LogReg':>9}   per-round RF")
for name, cols in featsets.items():
    X = np.array([[r[c] for c in cols] for r in rows], dtype=float)
    rf_scores, lr_scores = [], []
    for held in sorted(set(grp)):
        tr, te = grp != held, grp == held
        rf = RandomForestClassifier(n_estimators=400, random_state=0).fit(X[tr], y[tr])
        lr = make_pipeline(StandardScaler(),
                           LogisticRegression(max_iter=2000)).fit(X[tr], y[tr])
        rf_scores.append(rf.score(X[te], y[te]))
        lr_scores.append(lr.score(X[te], y[te]))
    print(f"{name:<20} {np.mean(rf_scores):12.3f} {np.mean(lr_scores):9.3f}   "
          f"{[f'{s:.2f}' for s in rf_scores]}")

print(f"\nbaselines: majority class {max(np.bincount(y.astype(int)))/len(y):.3f}, "
      f"random {1/len(set(y)):.3f}")

# confusion for the best simple model
best = ['peak_hpf', 'swing_peak_to_trough']
X = np.array([[r[c] for c in best] for r in rows], dtype=float)
from sklearn.metrics import confusion_matrix
preds = np.empty_like(y)
for held in sorted(set(grp)):
    tr, te = grp != held, grp == held
    m = RandomForestClassifier(n_estimators=400, random_state=0).fit(X[tr], y[tr])
    preds[te] = m.predict(X[te])
labs = sorted(set(y))
cm = confusion_matrix(y, preds, labels=labs)
print(f"\n=== confusion, {best} (all rows, each predicted by a model that never saw its round) ===")
print('      ' + ''.join(f'{l:>6.0f}' for l in labs) + '   recall')
for i, l in enumerate(labs):
    print(f'{l:>4.0f}  ' + ''.join(f'{v:>6}' for v in cm[i]) +
          f'   {cm[i,i]/cm[i].sum():.2f}')

err = np.abs(preds - y)
print(f"\nmean absolute error: {err.mean():.2f} in;  "
      f"within 1in: {100*(err<=1).mean():.1f}%;  exact: {100*(err==0).mean():.1f}%")
