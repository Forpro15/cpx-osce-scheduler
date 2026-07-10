#!/usr/bin/env python3
"""
CPX/OSCE 실기 연습 자동배정 — 참조 구현 (config 기반, 개인정보 비의존)

build_master.py의 그리디 배정 로직을 학교 독립적으로 일반화한 것입니다.
입력은 config/schedule.yaml 하나이며, 학생 개인정보는 전혀 사용하지 않습니다
(조 번호와 조별 인원수만 사용).

usage:
    python scheduler.py config/schedule.yaml
    python scheduler.py config/schedule.yaml --csv out/schedule.csv
"""
import sys, csv, argparse, datetime as dt
from collections import defaultdict

try:
    import yaml
except ImportError:
    sys.exit("PyYAML 필요: pip install pyyaml")


def as_date(v):
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 조 -> 인원수
    sizes = {}
    for grp in cfg["groups"]:
        for gid in grp["ids"]:
            sizes[gid] = grp["size"]

    period = (as_date(cfg["period"]["start"]), as_date(cfg["period"]["end"]))
    op = cfg["operating_classes"]
    cap = cfg["capacity"]

    blackout = {as_date(b["date"]) for b in cfg.get("cpx_blackout_dates", [])}

    osce_place = {}
    for d, mapping in cfg.get("osce", {}).get("placement", {}).items():
        for cls, gid in mapping.items():
            osce_place[(as_date(d), int(cls))] = gid

    balance = cfg.get("balance_goals", {})
    return {
        "sizes": sizes,
        "period": period,
        "weekday_classes": op["weekday"],
        "weekend_classes": op["weekend"],
        "rooms_total": cap["rooms_total"],
        "people_per_room": cap.get("people_per_room", 2),
        "max_per_class": cap["max_people_per_class"],
        "max_groups_per_class": cap.get("max_groups_per_class", 6),
        "blackout": blackout,
        "osce_place": osce_place,
        "target": balance.get("target_practice_count"),
    }


def assign(c):
    sizes = c["sizes"]
    groups = sorted(sizes)
    start, end = c["period"]
    days = [start + dt.timedelta(i) for i in range((end - start).days + 1)]

    # OSCE로 그날 묶인 조 (하루 1 Class 원칙 → 그날 CPX 제외)
    osce_busy = defaultdict(set)
    for (d, _cls), gid in c["osce_place"].items():
        osce_busy[d].add(gid)

    # 목표 연습 횟수: 미지정 시 전체 슬롯 용량으로부터 균등 상한 추정
    target = c["target"]
    if target is None:
        slots = sum(
            len(c["weekend_classes"] if d.weekday() >= 5 else c["weekday_classes"])
            for d in days if d not in c["blackout"]
        )
        target = (slots * c["max_groups_per_class"]) // len(groups)

    counts = {g: 0 for g in groups}
    class_counts = defaultdict(int)      # (group, class) -> 횟수
    last_class = {g: None for g in groups}
    assignment = {}                       # (date, class) -> [group,...]

    for d in days:
        if d in c["blackout"]:
            continue
        classes = c["weekend_classes"] if d.weekday() >= 5 else c["weekday_classes"]
        day_assigned = set()
        for cls in classes:
            people, chosen = 0, []
            while len(chosen) < c["max_groups_per_class"]:
                cands = [
                    g for g in groups
                    if g not in osce_busy[d] and g not in day_assigned and g not in chosen
                    and counts[g] < target
                    and people + sizes[g] <= c["max_per_class"]
                    and (people + sizes[g]) // c["people_per_room"] <= c["rooms_total"]
                ]
                if not cands:
                    break
                # 균등 우선순위: (해당 Class 경험 적은 순, 총 횟수 적은 순,
                #                 직전과 같은 Class면 후순위, 조 번호)
                cands.sort(key=lambda g: (
                    class_counts[(g, cls)], counts[g],
                    1 if last_class[g] == cls else 0, g))
                g = cands[0]
                chosen.append(g); people += sizes[g]
                counts[g] += 1; class_counts[(g, cls)] += 1
                day_assigned.add(g); last_class[g] = cls
            if chosen:
                assignment[(d, cls)] = chosen
    return assignment, counts, class_counts, target


def verify(c, assignment, counts):
    checks = []
    vals = list(counts.values())
    checks.append(("조별 연습 횟수 편차 = 0", max(vals) - min(vals) == 0))
    ok_cap = all(
        sum(c["sizes"][g] for g in gs) <= c["max_per_class"]
        for gs in assignment.values())
    checks.append((f"Class 인원 <= {c['max_per_class']}", ok_cap))
    ok_room = all(
        sum(c["sizes"][g] for g in gs) // c["people_per_room"] <= c["rooms_total"]
        for gs in assignment.values())
    checks.append((f"방 개수 <= {c['rooms_total']}", ok_room))
    ok_black = all(d not in c["blackout"] for (d, _cls) in assignment)
    checks.append(("제외일 CPX 배정 = 0", ok_black))
    ok_day = True
    per_day = defaultdict(list)
    for (d, _cls), gs in assignment.items():
        per_day[d].extend(gs)
    for d, gs in per_day.items():
        if len(gs) != len(set(gs)):
            ok_day = False
    checks.append(("하루 1 Class (조 중복 없음)", ok_day))
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--csv")
    a = ap.parse_args()

    c = load_config(a.config)
    assignment, counts, class_counts, target = assign(c)

    print(f"목표 연습 횟수(target) = {target}\n")
    print("조 | 인원 | 총횟수 | Class별")
    for g in sorted(counts):
        by = " ".join(f"C{cls}:{class_counts[(g, cls)]}"
                      for cls in sorted({k[1] for k in class_counts}))
        print(f"{g:2d} | {c['sizes'][g]:2d}  | {counts[g]:3d}   | {by}")
    print(f"\n총 배정 = {sum(counts.values())}, 사용 슬롯 = {len(assignment)}\n")

    print("[검산]")
    all_ok = True
    for name, ok in verify(c, assignment, counts):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        all_ok &= ok
    print("\n결과:", "모든 하드 제약 충족" if all_ok else "위반 있음 — 설정 확인 필요")

    if a.csv:
        import os
        os.makedirs(os.path.dirname(a.csv) or ".", exist_ok=True)
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "weekday", "class", "groups", "people"])
            for (d, cls) in sorted(assignment):
                gs = assignment[(d, cls)]
                w.writerow([d.isoformat(), "월화수목금토일"[d.weekday()],
                            cls, " ".join(map(str, gs)),
                            sum(c["sizes"][g] for g in gs)])
        print(f"\nCSV 저장: {a.csv}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
