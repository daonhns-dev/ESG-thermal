"""Human-blinded E3 review and selection comparison on existing E1 exports."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

import cv2
import numpy as np

from aggregate_14_pairs import OUTPUT, PROJECT, directory
from dev_labels import load_labels
from e3_rules import matches, stationary_match
from ego_motion import load_run, open_frames, same_object, select_distinct
from heldout_eval import _select_tracks_across_frames, context_strip, display
from prepare_visual_review import COLORS, coordinates, label_image, write_json, write_png
from heatpipe_service.analysis.io import TEMP_SCALE
from heatpipe_service.analysis.rendering import to_grayscale


OCTOBER = ("20261026_005923", "20261029_002254")
CLASSES = {"vehicle", "road", "cover", "other", "uncertain", "artifact"}
PAIR_FIELDS = ("same_source", "a_visible_in_b", "b_visible_in_a", "a_cover", "b_cover")
ANSWERS = {"yes", "no", "uncertain"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def track_id(row):
    return row["summary"]["track_id"]


def open_runs():
    overnight = OUTPUT / "overnight-20261116-v2"
    aggregate = read(overnight / "aggregate_14_pairs.json")
    paths = {row["stem"]: directory(row["stem"], "E1", overnight)
             for row in aggregate if row["skipped_frames"] == 0}
    paths.update({stem: OUTPUT / "heldout-20261118/e1" / stem for stem in OCTOBER})
    return {stem: load_run(path) for stem, path in sorted(paths.items())}


def baseline(tracks, frames):
    return _select_tracks_across_frames(tracks, frames["start"], frames["end_exclusive"], 50)


def existing_labels():
    labels, _ = load_labels()
    review = OUTPUT / "heldout-20261118/review"
    october = {r["review_id"]: r for r in read(review / "review_labels.json")}
    sources = {r["review_id"]: (r["stem"], r["track_id"])
               for r in read(review / "review_manifest.json")}
    labels.update({key: october[id_]["label"] for id_, key in sources.items()})
    return labels, sources, october


def initial_pairs(runs, labels, sources, october):
    pairs = [(sources["H043"], sources["H025"], "mixed")]
    pairs.extend((sources[id_], sources["H021"], "stationary")
                 for id_, row in october.items() if id_ != "H021" and "H021" in row["note"])
    rankings = read(OUTPUT / "dev-motion-20261118/validate-0.70.json")["ranking"]
    for stem in ("20260814_122052", "20260817_225314"):
        rows = {track_id(row): row for row in runs[stem][0]}
        chosen = rankings[stem]["e1"]["track_ids"]
        kept = rankings[stem]["e1+distinct3"]["track_ids"]
        for tid in chosen:
            if tid in kept or labels.get((stem, tid)) != "road":
                continue
            peers = [j for j in kept if same_object(rows[tid], rows[j])]
            if not peers:
                raise ValueError(f"Missing replacement for {stem}/{tid}")
            pairs.extend(((stem, tid), (stem, j), "august_replacement") for j in peers)
        vehicles = [i for i in chosen if labels.get((stem, i)) == "vehicle"]
        repeat = next(((a, b) for i, a in enumerate(vehicles)
                       for b in vehicles[:i] if same_object(rows[a], rows[b])), None)
        if repeat:
            pairs.append(((stem, repeat[0]), (stem, repeat[1]), "vehicle_repeat"))
    return pairs


def render(root, cards, runs, october_dir):
    sheets, context = root / "sheets", root / "context"
    sheets.mkdir(parents=True)
    context.mkdir()
    by_stem, images = defaultdict(list), {}
    for row in cards:
        by_stem[row["stem"]].append(row)
    for stem, group in sorted(by_stem.items()):
        tracks, _, frame_range = runs[stem]
        by_id = {track_id(row): row for row in tracks}
        path = (october_dir if stem in OCTOBER else PROJECT / "data/experiments/raw" / stem[:8])
        frames = open_frames(path / f"{stem}.att", frame_range["total_att_frames"])
        for row in group:
            tag, track = row["id"], by_id[row["track_id"]]
            frame = track["representative_frame"]
            raw = frames[frame].astype(np.float32) / TEMP_SCALE
            images[tag] = label_image(display(raw), tag, [("", coordinates(track["representative"]), COLORS[2])])
            write_png(context / f"{tag}.png", context_strip(frames, track))
            if row.get("extra_context"):
                frame, point = min(track["points"], key=lambda p: abs(p[0] - row["extra_context"]))
                shifted = {**track, "representative_frame": frame, "representative": point}
                write_png(context / f"{tag}-extra.png", context_strip(frames, shifted))
        print(f"{stem}: {len(group)} cards", flush=True)
    for start in range(0, len(cards), 6):
        panels = [images[row["id"]] for row in cards[start:start + 6]]
        panels.extend(np.zeros_like(panels[0]) for _ in range(6 - len(panels)))
        write_png(sheets / f"sheet-{start // 6 + 1:02}.png",
                  np.vstack([np.hstack(panels[:3]), np.hstack(panels[3:])]))


def publish(root, entries, pairs, runs, october_dir, mixed=(), label_keys=None):
    rng = random.Random(1817)
    keys = sorted(entries)
    rng.shuffle(keys)
    ids = {key: f"R{i:03}" for i, key in enumerate(keys, 1)}
    cards = [{"id": ids[key], "stem": key[0], "track_id": key[1], "groups": sorted(entries[key]),
              "extra_context": 3384 if key in mixed else None} for key in keys]
    pairs = list(pairs)
    rng.shuffle(pairs)
    questions = []
    for i, (a, b, reason) in enumerate(pairs, 1):
        if rng.randrange(2):
            a, b = b, a
        questions.append({"pair_id": f"P{i:02}", "a": ids[a], "b": ids[b], "reason": reason})
    blind, private = root / "reviewer", root / "internal"
    root.mkdir(parents=True)
    render(blind, cards, runs, october_dir)
    private.mkdir()
    write_json(private / "manifest.json", cards)
    write_json(private / "pairs.json", questions)
    classify = {ids[key] for key in label_keys} if label_keys is not None else set(ids.values())
    write_json(private / "classification_ids.json", sorted(classify))
    write_json(blind / "candidate_labels.json", {"reviewer_type": "", "labels": [
        {"id": row["id"], "label": "", "note": ""} for row in cards if row["id"] in classify
    ]})
    write_json(blind / "pair_labels.json", {"reviewer_type": "", "labels": [
        {k: row[k] for k in ("pair_id", "a", "b")} | {k: "" for k in (*PAIR_FIELDS, "note")}
        for row in questions
    ]})
    lines = ["# 사람 판독", "",
             "이 폴더의 시트와 context 폴더를 사용합니다. 중앙 이미지는 대표 프레임입니다.",
             "가운데 +0의 분홍색 사각형이 가리키는 열점을 분류합니다. 앞뒤 프레임도 참고합니다.", "",
             "- `road` · 주변보다 분명히 따뜻한 국소 열점이 노면 위에 보임",
             "- `uncertain` · 표시된 대상을 특정하기 어려움",
             "- `cover` · 맨홀·덮개",
             "- `vehicle` · 차량, 오토바이, 배기구 등 차량 관련 열원",
             "- `other` · 난간, 건물 등 별도로 식별되는 기타 열원",
             "- `artifact` · 센서 또는 영상 처리에서 생긴 영상 이상", "",
             "쌍의 항목은 yes, no, uncertain으로 기록합니다. A가 B에 보이는지는 B의 중앙 이미지로 판단합니다.",
             "직접 판독한 사람은 두 양식의 reviewer_type에 human을 기록합니다.", "",
             "[후보 판독](candidate_labels.json) · [후보 쌍 판독](pair_labels.json)", ""]
    for i, path in enumerate(sorted((blind / "sheets").glob("*.png"))):
        links = " · ".join(f"[{row['id']}](context/{row['id']}.png)" for row in cards[i*6:i*6+6])
        lines.append(f"- [{path.stem}](sheets/{path.name}) · {links}")
    lines.extend(["", "## 후보 쌍", ""])
    for row in questions:
        links = [f"[{row[side]}](context/{row[side]}.png)" for side in ("a", "b")]
        lines.append(f"- {row['pair_id']} · A {links[0]} · B {links[1]}")
        for side in ("a", "b"):
            tag = row[side]
            if (blind / "context" / f"{tag}-extra.png").exists():
                lines.append(f"  - [추가 프레임 {tag}](context/{tag}-extra.png)")
    (blind / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(cards)} cards, {len(questions)} pairs", flush=True)


def prepare(args):
    runs = open_runs()
    labels, sources, october = existing_labels()
    pairs = initial_pairs(runs, labels, sources, october)
    rng = random.Random(1817)
    entries = {sources[id_]: set() for id_ in ("H007", "H043", "H086", "H111")}
    for a, b, _ in pairs:
        entries.setdefault(a, set())
        entries.setdefault(b, set())
    for stem, (tracks, _, frames) in runs.items():
        selected = {track_id(r) for r in baseline(tracks, frames)}
        pool = [track_id(r) for r in tracks[50:300] if track_id(r) not in selected]
        for tid in rng.sample(pool, args.sample_per_file):
            entries.setdefault((stem, tid), set()).add("ranks_51_300")
    publish(args.output, entries, pairs, runs, args.october_dir, (sources["H043"], sources["H025"]))


def score(args):
    root = args.output
    cards = read(root / "internal/manifest.json")
    questions = read(root / "internal/pairs.json")
    candidate = read(root / "reviewer/candidate_labels.json")
    paired = read(root / "reviewer/pair_labels.json")
    if candidate["reviewer_type"] != "human" or paired["reviewer_type"] != "human":
        raise ValueError("Set reviewer_type to human after human review")
    entries = {row["id"]: row["label"] for row in candidate["labels"]}
    classification_ids = set(read(root / "internal/classification_ids.json"))
    if len(entries) != len(candidate["labels"]) or set(entries) != classification_ids:
        raise ValueError("Candidate IDs do not match the manifest")
    if any(label not in CLASSES and not (args.partial and label == "") for label in entries.values()):
        raise ValueError("Missing or invalid human candidate label")
    entries = {tag: label for tag, label in entries.items() if label}
    results = {row["pair_id"]: row for row in paired["labels"]}
    if len(results) != len(paired["labels"]) or set(results) != {r["pair_id"] for r in questions}:
        raise ValueError("Pair IDs do not match the manifest")
    by_id = {r["id"]: r for r in cards}
    pairs = []
    for row in questions:
        answer = results[row["pair_id"]]
        if any(answer[k] != row[k] for k in ("a", "b")) or any(answer[k] not in ANSWERS for k in PAIR_FIELDS):
            raise ValueError(f"Missing or invalid pair judgment: {row['pair_id']}")
        pairs.append({**row, **answer, **{
            f"{side}_key": [by_id[row[side]][k] for k in ("stem", "track_id")] for side in ("a", "b")
        }})
    counts = defaultdict(Counter)
    sample_sizes = Counter()
    for row in cards:
        if "ranks_51_300" in row["groups"]:
            stem = row["stem"]
            sample_sizes[stem] += 1
            if row["id"] in entries:
                counts[stem][entries[row["id"]]] += 1
    pooled = sum(counts.values(), Counter())

    def fraction(c, total):
        n = sum(c.values())
        return {"n": n, "sample_size": total,
                "road_fraction": c["road"] / n if n and n == total else None, "counts": c}

    write_json(root / "internal/human_review_scored.json", {
        "by_file": {stem: fraction(counts[stem], size) for stem, size in sample_sizes.items()},
        "pooled": fraction(pooled, sum(sample_sizes.values())),
        "candidate_labels": [{**r, "label": entries[r["id"]]} for r in cards if r["id"] in entries],
        "pairs": pairs,
    })


def agreement(args):
    root = args.output
    before = read(root / "internal/candidate_labels_R001-R020_round1.json")
    after = read(root / "reviewer/candidate_labels_R001-R020_round2.json")
    expected = {f"R{i:03d}" for i in range(1, 21)}
    for form in (before, after):
        rows = form["labels"]
        if (form["reviewer_type"] != "human" or len(rows) != len(expected)
                or {r["id"] for r in rows} != expected or any(r["label"] not in CLASSES for r in rows)):
            raise ValueError("Each round requires 20 valid human labels for R001–R020")
    previous = {r["id"]: r["label"] for r in before["labels"]}
    current = {r["id"]: r["label"] for r in after["labels"]}
    transitions = {label: Counter(current[tag] for tag in expected if previous[tag] == label)
                   for label in sorted(set(previous.values()))}
    changed = [{"id": tag, "before": previous[tag], "after": current[tag]}
               for tag in sorted(expected) if previous[tag] != current[tag]]
    count = len(expected) - len(changed)
    write_json(root / "internal/label_agreement_R001-R020.json", {
        "comparison": "label_definition_change_same_reviewer",
        "n": len(expected), "agreed": count, "agreement": count / len(expected),
        "transitions": transitions, "changed": changed,
    })
    print(f"Agreement: {count}/{len(expected)} ({count / len(expected):.0%})")


def zoom_strip(frames, track):
    center = track["representative_frame"]
    indices = [min(max(center + offset, 0), len(frames) - 1) for offset in (-6, -3, 0, 3, 6)]
    points = dict(track["points"])
    boxes = [coordinates(points[index]) if index in points else None for index in indices]
    x0, y0, x1, y1 = np.array([box for box in boxes if box is not None]).T
    height, width = frames.shape[1:]
    left, top = max(0, x0.min() - 16), max(0, y0.min() - 16)
    right, bottom = min(width, x1.max() + 17), min(height, y1.max() + 17)
    crops = np.stack([frames[i, top:bottom, left:right] for i in indices]).astype(np.float32) / TEMP_SCALE
    plausible = crops[crops < 200]
    low, high = np.percentile(plausible if plausible.size else crops, (5, 95))
    high = max(high, low + 0.25)
    panels = []
    for index, crop, box in zip(indices, crops, boxes, strict=True):
        image = to_grayscale(crop, float(low), float(high))
        image = cv2.resize(image, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        marks = []
        if box is not None:
            a, b, c, d = box
            marks.append(("", (3*(a-left), 3*(b-top), 3*(c-left), 3*(d-top)), COLORS[2]))
        if image.shape[1] < 200:
            image = cv2.copyMakeBorder(image, 0, 0, 0, 200 - image.shape[1], cv2.BORDER_CONSTANT)
        panels.append(label_image(image, f"{index-center:+d} | {low:.2f}..{high:.2f} C", marks))
    return np.hstack(panels)


def write_zoom_review(review, tags):
    lines = ["# 전체 장면과 확대 이미지 판독", "",
             "위쪽은 전체 장면, 아래쪽은 같은 시점의 후보 확대입니다. "
             "위쪽 +0의 분홍색 사각형으로 위치와 주변 물체를 확인한 뒤 아래쪽의 열점 모양을 참고합니다.",
             "아래쪽 5개 프레임은 후보 주변에 맞춘 동일한 표시 온도 범위를 사용합니다. "
             "확대에서도 대상이나 열점이 분명하지 않으면 uncertain으로 기록합니다.", "",
             "[기존 라벨 정의](../REVIEW.md)를 적용합니다. "
             "[판독 양식](zoom_labels.json)의 zoom_label을 작성하고 reviewer_type을 human으로 설정합니다.", ""]
    for tag in sorted(tags):
        context = cv2.imdecode(np.fromfile(review.parent / "context" / f"{tag}.png", np.uint8), cv2.IMREAD_COLOR)
        detail = cv2.imdecode(np.fromfile(review / f"{tag}.png", np.uint8), cv2.IMREAD_COLOR)
        pairs = []
        for full_panel, detail_panel in zip(np.hsplit(context, 5), np.hsplit(detail, 5), strict=True):
            width = max(full_panel.shape[1], detail_panel.shape[1])
            full_panel, detail_panel = [
                cv2.copyMakeBorder(panel, 0, 0, (width - panel.shape[1]) // 2,
                                   (width - panel.shape[1] + 1) // 2, cv2.BORDER_CONSTANT)
                for panel in (full_panel, detail_panel)
            ]
            pairs.append(np.vstack((full_panel, detail_panel)))
        write_png(review / f"{tag}-paired.png", np.hstack(pairs))
        lines.append(f"- [{tag} 전체 장면 + 확대]({tag}-paired.png)")
    (review / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def zoom(args):
    root = args.output
    agreement_rows = read(root / "internal/label_agreement_R001-R020.json")["changed"]
    pilot_ids = {r["id"] for r in agreement_rows if r["before"] == "road" and r["after"] == "uncertain"}
    if not pilot_ids:
        raise ValueError("The completed comparison has no road-to-uncertain cases")
    cards = [r for r in read(root / "internal/manifest.json") if r["id"] in pilot_ids]
    review = root / "reviewer/zoom-pilot"
    if review.exists():
        raise FileExistsError(review)
    runs = open_runs()
    paths = {stem: ((args.october_dir if stem in OCTOBER else
                     PROJECT / "data/experiments/raw" / stem[:8]) / f"{stem}.att")
             for stem in {r["stem"] for r in cards}}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    panels = {}
    for stem, path in paths.items():
        tracks, _, meta = runs[stem]
        frame_data = open_frames(path, meta["total_att_frames"])
        by_id = {track_id(row): row for row in tracks}
        for row in cards:
            if row["stem"] == stem:
                panels[row["id"]] = zoom_strip(frame_data, by_id[row["track_id"]])
    review.mkdir()
    for tag, panel in sorted(panels.items()):
        write_png(review / f"{tag}.png", panel)
    write_json(review / "zoom_labels.json", {"reviewer_type": "", "labels": [
        {"id": tag, "original_label": "uncertain", "zoom_label": "", "note": ""} for tag in sorted(panels)
    ]})
    write_zoom_review(review, panels)
    print(f"{len(panels)} zoom sequences for human review")


def zoom_score(args):
    root = args.output
    form = read(root / "reviewer/zoom-pilot/zoom_labels.json")
    changed = read(root / "internal/label_agreement_R001-R020.json")["changed"]
    expected = {r["id"] for r in changed if r["before"] == "road" and r["after"] == "uncertain"}
    rows = form["labels"]
    if (not expected or form["reviewer_type"] != "human" or len(rows) != len(expected)
            or {r["id"] for r in rows} != expected
            or any(r["original_label"] != "uncertain" or r["zoom_label"] not in CLASSES for r in rows)):
        raise ValueError("The zoom pilot requires completed human judgments for all pilot IDs")
    counts = Counter(r["zoom_label"] for r in rows)
    resolved = sum(counts[label] for label in ("road", "cover", "other"))
    write_json(root / "internal/zoom_pilot_scored.json", {
        "n": len(rows), "zoom_counts": counts, "uncertain_to_road_cover_other": resolved,
        "uncertain_to_road_cover_other_fraction": resolved / len(rows), "labels": rows,
    })
    print(f"Uncertain to road/cover/other: {resolved}/{len(rows)}")


def rank_sample(args):
    root = args.output
    read(root / "internal/zoom_pilot_scored.json")
    review = root / "reviewer/rank-sample"
    if review.exists():
        raise FileExistsError(review)
    completed = read(root / "reviewer/candidate_labels_R001-R020_round2.json")
    done = {r["id"] for r in completed["labels"]}
    runs = open_runs()
    ranks = {(stem, track_id(row)): rank for stem, (tracks, _, _) in runs.items()
             for rank, row in enumerate(tracks[50:300], 51)}
    sample = [{**row, "rank": ranks[row["stem"], row["track_id"]]}
              for row in read(root / "internal/manifest.json") if "ranks_51_300" in row["groups"]]
    pending = [row["id"] for row in sample if row["id"] not in done]
    if any(not (review.parent / "context" / f"{tag}.png").is_file() for tag in pending):
        raise FileNotFoundError("Missing original context images")
    review.mkdir()
    write_json(root / "internal/rank_sample_manifest.json", {"format": "original_context", "cards": sample})
    write_json(review / "labels.json", {"reviewer_type": "", "labels": [
        {"id": tag, "label": "", "note": ""} for tag in pending
    ]})
    lines = ["# E1 순위 표본 판독", "",
             f"이전 판독을 제외한 {len(pending)}건입니다. 판독 형식은 원래 5프레임 이미지로 고정합니다.",
             "가운데 +0의 분홍색 사각형을 기준으로 [기존 라벨 정의](../REVIEW.md)를 적용합니다.",
             "[판독 양식](labels.json)에 결과를 적고, 완료 후 reviewer_type을 human으로 표시합니다.", ""]
    lines.extend(f"- [{tag}](../context/{tag}.png)" for tag in pending)
    (review / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(pending)} pending from {len(sample)} sampled candidates")


def rank_sample_score(args):
    root = args.output
    cards = read(root / "internal/rank_sample_manifest.json")["cards"]
    forms = [read(root / name) for name in (
        "reviewer/candidate_labels_R001-R020_round2.json", "reviewer/rank-sample/labels.json")]
    entries = {}
    for form in forms:
        if form["reviewer_type"] != "human":
            raise ValueError("Rank sample requires human judgments")
        for row in form["labels"]:
            if row["id"] in entries or row["label"] not in CLASSES:
                raise ValueError(f"Duplicate or invalid judgment: {row['id']}")
            entries[row["id"]] = row["label"]
    groups, files, by_file_and_band = defaultdict(Counter), defaultdict(Counter), defaultdict(lambda: defaultdict(Counter))
    for card in cards:
        rank = card["rank"]
        band = "51–100" if rank <= 100 else "101–200" if rank <= 200 else "201–300"
        label = entries[card["id"]]
        groups[band][label] += 1
        files[card["stem"]][label] += 1
        by_file_and_band[card["stem"]][band][label] += 1

    def summarize(counts):
        return {key: {"n": counts[key].total(), "counts": counts[key],
                      "road_fraction": counts[key]["road"] / counts[key].total()}
                for key in sorted(counts)}

    history, _, _ = existing_labels()
    top = {}
    for stem, (tracks, _, frames) in open_runs().items():
        selected = baseline(tracks, frames)
        counts = Counter(history.get((stem, track_id(row)), "unreviewed") for row in selected)
        top[stem] = {"n": len(selected), "counts": counts,
                     "road_fraction": counts["road"] / counts.total() if not counts["unreviewed"] else None}
    write_json(root / "internal/rank_sample_scored.json", {
        "by_rank_band": summarize(groups), "by_file": summarize(files),
        "by_file_and_rank_band": {stem: summarize(counts) for stem, counts in by_file_and_band.items()},
        "historical_top50": top,
    })


def compare(args):
    runs = open_runs()
    previous, _, _ = existing_labels()
    reports = [read(path) for path in args.human_report]
    human, pair_labels = {}, {}
    for reviewed in reports:
        human.update({(r["stem"], r["track_id"]): r["label"] for r in reviewed["candidate_labels"]})
        for row in reviewed["pairs"]:
            a, b = tuple(row["a_key"]), tuple(row["b_key"])
            pair_labels[a, b] = row["same_source"], row["a_visible_in_b"]
            pair_labels[b, a] = row["same_source"], row["b_visible_in_a"]
    stopped = read(args.stationary) if args.stationary else {}
    output, extras, extra_pairs = {}, set(), set()
    for stem, (tracks, _, frames) in runs.items():
        first, last = frames["start"], frames["end_exclusive"]
        control_ids = [track_id(r) for r in baseline(tracks, frames)]
        control = set(control_ids)
        roads = {t for t in control if human.get((stem, t), previous.get((stem, t))) == "road"}
        stop = stopped.get(stem, [])
        candidates = {"legacy": None, "overlap_50": partial(matches, share=0.5),
                      "representative_6": partial(matches, representative_radius=6),
                      "combined": partial(matches, share=0.5, representative_radius=6)}
        if args.stationary:
            candidates["representative_6_stationary"] = lambda a, b: (
                matches(a, b, representative_radius=6) or stationary_match(a, b, stop)
            )
        methods = {}
        output[stem] = {"e1_track_ids": control_ids, "e1_road": sorted(roads), "methods": methods}
        for name, matcher in candidates.items():
            blocked = {}
            chosen, skipped = select_distinct(tracks, first, last, 50, match=matcher,
                                              on_skip=lambda a, b: blocked.update({track_id(a): track_id(b)}))
            ids = [track_id(row) for row in chosen]
            selected = set(ids)
            new = selected - control
            unread = {t for t in new if (stem, t) not in previous and (stem, t) not in human}
            extras.update((stem, t) for t in unread)
            # Historical labels flag road candidates; human judgments measure actual visibility.
            reviewed_pair_count = sum(((stem, a), (stem, b)) in pair_labels
                                      for i, a in enumerate(ids) for b in ids[:i])
            duplicate = sum(any(pair_labels.get(((stem, a), (stem, b))) == ("yes", "yes")
                                for b in ids[:i]) for i, a in enumerate(ids))
            reviewed_blocks = [pair_labels[(stem, a), (stem, b)] for a, b in blocked.items()
                               if ((stem, a), (stem, b)) in pair_labels]
            for a in roads - selected:
                b = blocked.get(a)
                if b is not None and ((stem, a), (stem, b)) not in pair_labels:
                    extra_pairs.add(((stem, a), (stem, b)))
            methods[name] = {
                "track_ids": ids, "rule_skips": skipped, "blocked_by_track": blocked,
                "existing_road_tracks_selected": sorted(roads & selected),
                "existing_road_tracks_dropped": sorted(roads - selected),
                "new_unread_tracks": sorted(unread),
                "human_labeled_e1_road_dropped": sorted(t for t in control - selected
                                                       if human.get((stem, t)) == "road"),
                "human_duplicate_lower_bound": duplicate if reviewed_pair_count else None,
                "human_wrong_source_lower_bound": sum(same == "no" for same, _ in reviewed_blocks)
                                                  if reviewed_blocks else None,
                "human_hidden_source_lower_bound": sum(visible == "no" for _, visible in reviewed_blocks)
                                                   if reviewed_blocks else None,
                "human_reviewed_selected_pairs": reviewed_pair_count,
                "human_reviewed_suppressions": len(reviewed_blocks),
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, {"files": output, "human_review_supplied": bool(reports),
                             "stationary_supplied": args.stationary is not None,
                             "unread_union": sorted(extras),
                             "unresolved_road_pairs": sorted(extra_pairs)})
    print(f"{len(output)} full E1 files, {len(extras)} distinct unread entrants", flush=True)


def extra(args):
    report = read(args.comparison)
    entries = {tuple(pair): {"new_only"} for pair in report["unread_union"]}
    classify = set(entries)
    pairs = [(tuple(a), tuple(b), "road_visibility")
             for a, b in report["unresolved_road_pairs"]]
    for a, b, _ in pairs:
        entries.setdefault(a, set())
        entries.setdefault(b, set())
    if not entries:
        print("No new unread candidates")
        return
    publish(args.output, entries, pairs, open_runs(), args.october_dir, label_keys=classify)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name, fn in (("prepare", prepare), ("score", score), ("agreement", agreement),
                     ("zoom", zoom), ("zoom-score", zoom_score),
                     ("rank-sample", rank_sample), ("rank-sample-score", rank_sample_score),
                     ("compare", compare), ("extra", extra)):
        command = actions.add_parser(name)
        command.add_argument("output", type=Path)
        command.set_defaults(run=fn)
        if name in ("prepare", "extra", "zoom"):
            command.add_argument("--october-dir", type=Path, default=Path("E:/heatpipe/datasets_TB"))
        if name == "prepare":
            command.add_argument("--sample-per-file", type=int, choices=range(30, 51), default=30, metavar="30..50")
        if name == "score":
            command.add_argument("--partial", action="store_true", help="Accept blank candidate labels")
        if name == "compare":
            command.add_argument("--human-report", type=Path, action="append", default=[])
            command.add_argument("--stationary", type=Path)
        if name == "extra":
            command.add_argument("comparison", type=Path)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
