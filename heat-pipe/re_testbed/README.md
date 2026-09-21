# re_testbed

`re_testbed` is the new review pipeline for this project. The `re_` prefix
means "review and re-scoped TESTBED work": this package supports the revised
scope, not a second physical testbed.

It is not a pipeline for automatic district heating pipe defect detection.
Its scope is to help reviewers find, rank, and inspect thermal anomaly candidates
inside a registered TESTBED driving section or user-defined ROI.

The final decision remains a field-verification and supervisor workflow.

## Scope

- Input: registered driving data, thermal frames, optional RGB frames, timestamp/GPS metadata, and reviewer-selected ROI.
- Output: ranked thermal anomaly candidates and review artifacts.
- Non-goal: automatically confirming pipe leakage, pipe defect, or exact buried-pipe location.

## Main Track

The main product-aligned flow is single-session ROI review:

1. `single_frame_review.py`
   - Review one thermal frame and one ROI.
   - Compute ROI-local robust z-score.
   - Extract connected thermal candidates.
   - Save a ranked candidate list and review panel.

2. `single_session_review.py`
   - Apply the same ROI-local candidate extraction across one session.
   - Track repeated frame-level detections into session-level candidate tracks.
   - Save `frame_candidates.csv`, `tracks.csv`, and `summary.json`.

This matches the prototype direction: one registered video/session goes in,
thermal anomaly candidates inside the selected ROI come out.

## Algorithm R&D

`algorithm_compare.py` is the first qualitative comparison tool for map-based
candidate detectors. It compares:

- `robust_zscore`
- `top_hat_zscore`
- `multiscale_top_hat`
- `percentile_contrast`

For each map-based method, compare both context modes when needed:

- `roi`: compute the score inside the selected prototype ROI.
- `road`: compute the score in the lower road area, then inspect the selected ROI.

This context comparison is intentional. It exposes whether an algorithm is
stable when the selected ROI is narrow or whether it depends heavily on extra
surrounding context.

Isolation Forest, LOF, and similar methods are not first-stage score-map
detectors in this workflow. Treat them as second-stage candidate rerankers over
features such as area, max score, mean score, peak temperature, edge sharpness,
circularity, persistence, and motion. They should be evaluated after map-based
candidates and a small reviewer label set exist.

`algorithm_track_compare.py` extends the same comparison over a frame range. It
uses the shared connected-component candidate schema and shared tracking logic
for every map-based algorithm, then writes:

- frame-level candidates
- algorithm-specific tracks
- pairwise candidate overlap between algorithms
- a top-track representative-frame panel
- a compact summary for selecting 30-50 labeling candidates

The track comparison also records `persistence_score`, a temporal score that
boosts candidates observed continuously across multiple frames. This reflects
the video setting: a meaningful road-surface thermal candidate should usually be
visible for more than a single frame as the vehicle approaches or passes it.

`track_quality_score` subtracts soft penalties from persistence rather than
hard-filtering candidates. Current penalties cover ROI/top-band boundary contact,
large relative area, unstable area changes, sharp edges, and low circularity.
This intentionally revives old sharp/diffuse shape cues as ranking signals, not
as hard gates, because true buried-pipe heat patches may be diffuse.

`label_candidate_export.py` prepares the next manual-label step. It merges
overlapping tracks across algorithms into case-level review items, then samples
a diverse label queue instead of simply taking top-N tracks. The sample includes
high-consensus cases, robust baseline cases, top-hat-only additions, sensitive
method-only additions, and mid-score non-boundary cases. This is intentional:
top-N tracks observed so far are often vehicles, structure edges, or boundary
responses, so top-N-only sampling can miss possible road-surface candidates.

Use these label choices first:

- `vehicle`
- `road_paint`
- `structure_edge`
- `road_surface_hotspot`
- `boundary_artifact`
- `unknown`

## 2026-09-14 Findings

The first labeled review set used three track-comparison ranges:

- `20260813_140209`, frames 21120-21160, grid ROI 4/1
- `20260813_140209`, frames 21200-21280, grid ROI 4/1
- `20260814_131222`, frames 21120-21160, grid ROI 4/1

The export merged 551 algorithm-specific tracks into 384 case-level candidates,
then selected 50 diverse labeling cases. The revised reviewer labels were:

- `vehicle`: 16
- `road_paint`: 23
- `road_surface_hotspot`: 11

Algorithm presence in the 50-case label set:

| Method | Target `road_surface_hotspot` | False positives | Target rate |
| --- | ---: | ---: | ---: |
| `robust_zscore` | 0 | 8 | 0.000 |
| `top_hat_zscore` | 4 | 19 | 0.174 |
| `multiscale_top_hat` | 8 | 27 | 0.229 |
| `percentile_contrast` | 6 | 18 | 0.250 |

Interpretation:

- There is no single clear replacement for robust z-score yet.
- Robust z-score is a conservative baseline, but it missed every labeled
  `road_surface_hotspot` in this small set.
- Top-hat and multi-scale top-hat methods add useful road-surface candidates,
  but also add road-paint and vehicle false positives.
- Percentile contrast has the highest target rate in this sample, but its
  strongest quality scores still often belong to vehicles and road paint.
- The better direction is multi-cue candidate generation followed by label-based
  reranking, not replacing robust z-score with one stronger score map.

Terminology:

- The current four methods are not ML/DL models. They are radiometric
  temperature-data statistical or image-processing algorithms.
- ML/DL expansion should start as second-stage candidate reranking after more
  labeled cases exist.

## Structural Limit

Single-session analysis cannot, by itself, prove whether a fixed heat source is
new, pipe-related, or simply a pre-existing road/urban feature. Stronger
algorithms can improve candidate ranking and known false-positive filtering, but
they cannot add missing information such as buried-pipe GIS coordinates,
ground-truth labels, or previous observations of the same location.

Until pipe GIS coordinates are available, the product wording should stay close to:

> Registered-route / ROI thermal anomaly candidate review

or:

> TESTBED/ROI thermal anomaly review support

Current observation note: in the first three inspected track-comparison ranges,
every method produced zero `likely_ground_fixed` tracks. Treat that as
supporting evidence for the current review-assist scope.

## Output Location

Generated panels, JSON logs, and CSV summaries should go under
`results/re_testbed/` by default. That directory is ignored by git in this
repository. Do not write generated review artifacts into the package directory.

## Implementation Rules

1. Reuse existing baseline logic before writing a new version.
   - Robust z-score, background estimation, edge sharpness, and circularity live in
     `utils/thermal_anomaly.py`.
   - Frame-to-frame candidate tracking lives in `utils/thermal_tracks.py`.
   - ROI/z-score domain helpers live in `re_testbed/roi.py`.
   - Older baseline scripts may still contain historical inline versions, but new work
     should import the shared utility functions.

2. Compute z-score inside the user-selected thermal-pipe ROI by default.
   - In the prototype workflow, the upper non-ground portion of the frame is
     excluded first, then the remaining lower frame is divided into 3 or 4
     equal review ROIs.
   - The selected ROI is the thermal-pipe review area, so `--analysis-mode roi`
     is the default and z-score is computed inside that ROI.
   - `--analysis-mode road` remains available only for diagnostics/comparison.
   - Use either explicit coordinates with `--roi x0,y0,x1,y1`, or prototype-style
     grid selection with `--roi-grid-count 3|4 --roi-grid-index N`.

3. Produce scores and rankings, not binary truth.
   - Candidate status should remain "review candidate" until field verification.
   - Ranking should help supervisors decide review order.

4. Benchmark only against small reviewer labels.
   - Before comparing robust z-score, multi-scale top-hat, Isolation Forest, LOF,
     or later ML/DL approaches, create a small label set from known false-positive
     classes such as vehicles, road paint, traffic lights, walls, and unknowns.
   - Without labels, benchmark results should be treated as qualitative review aids.

## Deferred Work

The earlier repeated-pass comparison files were removed from the active
`re_testbed` package because they no longer match the prototype-aligned flow.
That direction should be reopened only if new data makes it meaningful, such as:

- repeated passes over confirmed same road segments
- reliable RGB or thermal landmark registration
- pipe GIS coordinates or other route-level ground truth
- enough labeled candidate tracks to evaluate temporal-change hypotheses

Future ML/DL work should start from the label artifacts under
`results/re_testbed/label_candidates_grid4_idx1_z3/`, then test second-stage
reranking methods such as Isolation Forest, LOF, logistic regression, random
forest, or gradient boosting over candidate/track features.

## Files

- `configs/default.yaml`: default thresholds and execution flags.
- `background.py`: package wrapper for shared background and robust z-score helpers.
- `candidate.py`: connected-component candidate extraction and feature calculation.
- `single_frame_review.py`: prototype-aligned review for one frame and ROI.
- `single_session_review.py`: prototype-aligned review for one session and ROI.
- `algorithms.py`: map-based anomaly scoring algorithms.
- `algorithm_compare.py`: qualitative algorithm comparison panel for one frame.
- `algorithm_track_compare.py`: track-level comparison over a frame range.
- `label_candidate_export.py`: merge algorithm tracks and export diverse labeling candidates.
- `label_results_summary.py`: summarize reviewer labels by algorithm and label class.
- `score_and_rank.py`: shared ranking helper for candidate dictionaries.
