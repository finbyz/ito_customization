"""Read marked subject cells from ruled ITO student-list sheets (scans, photos, PDF renders).

Approach: find the column rules, remove the ruled lines, then detect the handwritten
ticks as ink blobs. Each blob is assigned to a column using the *local* (slanted) column
rules at the blob's own height, and blobs at the same height become one student row.
Because each student has at least one tick, tick-rows map to students in order. If the
number of tick-rows differs from the number of extracted students the grid is not
trusted and the caller falls back to manual review.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

STANDARD_COLUMNS = ["ISO", "IMO", "EIO", "GKIO", "NSSO", "ICO", "IDO", "NESO", "NHO", "NLRO"]


@dataclass
class GridLine:
    position: float
    slope: float

    def at(self, coordinate: float, reference: float) -> float:
        return self.position + self.slope * (coordinate - reference)


@dataclass
class GridRow:
    subjects: list = field(default_factory=list)   # marked column abbreviations, left to right
    free: list = field(default_factory=list)       # marked cells that carry an "F" (free subject)
    needs_review: bool = False                     # cancelled blob or tick sitting on a column rule


@dataclass
class GridResult:
    rows: list
    tick_rows: int
    column_counts: dict


def read_grid_subjects(image: bytes, columns: list[str], student_count: int) -> list[list[str]] | None:
    """Backward compatible: subjects per student, or None when the grid can't be trusted."""
    result = read_grid(image, columns, student_count)
    return [row.subjects for row in result.rows] if result else None


def read_grid(image: bytes, columns: list[str], student_count: int) -> GridResult | None:
    pixels = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if pixels is None or not columns or not student_count:
        return None
    if pixels.shape[0] > 1500:  # keep Hough/morphology fast and thresholds in range
        factor = 1400 / pixels.shape[0]
        pixels = cv2.resize(pixels, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    height, width = pixels.shape
    scale = height / 1190
    binary = cv2.adaptiveThreshold(
        pixels, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        max(15, int(31 * scale) | 1), 12,
    )
    segments = cv2.HoughLinesP(
        binary, 1, np.pi / 180, threshold=max(40, int(80 * scale)),
        minLineLength=max(80, int(190 * scale)), maxLineGap=max(10, int(30 * scale)),
    )
    if segments is None:
        return None
    segments = segments[:, 0]
    vertical = find_vertical_lines(segments, width, height, len(columns), scale)
    if not vertical:
        return None
    step, row_slope = find_row_step(segments, vertical, width, height, scale)
    if step is None:
        return None
    reference = height / 2
    ink = remove_rules(binary, vertical, scale, reference)
    top = find_body_top(ink, vertical, reference, height, scale, step)
    if top is None:
        return None
    blobs = find_tick_blobs(ink, vertical, top, reference, scale)
    if not blobs:
        return None
    for blob in blobs:  # undo the page tilt so ticks of one student share a height
        blob["ry"] = blob["cy"] - row_slope * (blob["cx"] - width * 0.6)
    # printed text (footer instructions) packs many blobs into one band; student rows have few ticks
    blobs = [
        blob for blob in blobs
        if sum(abs(other["ry"] - blob["ry"]) < 0.8 * step for other in blobs) < 8
    ]
    rows = [row for row in group_rows(blobs, step) if len({b["column"] for b in row}) < 6]
    # The TOTAL row sits below the last student row and is handwritten too.
    body = [b for row in rows[:student_count] for b in row]
    if len(rows) == student_count + 1 and body and looks_like_totals(rows[-1], body):
        rows = rows[:-1]  # handwritten TOTAL row below the last student
    if len(rows) != student_count:
        return GridResult(rows=[], tick_rows=len(rows), column_counts={})

    median_area = float(np.median([b["area"] for b in body]))
    median_width = float(np.median([b["width"] for b in body]))
    result_rows, counts = [], {code: 0 for code in columns}
    for row in rows:
        out = GridRow()
        seen = set()
        for blob in sorted(row, key=lambda item: item["cx"]):
            code = columns[blob["column"]]
            if blob["solid"] or blob["borderline"]:
                out.needs_review = True
            if blob["solid"]:
                continue  # scribbled-out cell is not a tick
            if code in seen:
                continue
            seen.add(code)
            out.subjects.append(code)
            counts[code] += 1
            if blob["width"] > 1.4 * median_width:  # tick followed by "F" is a free subject
                out.free.append(code)
        if not out.subjects:
            out.needs_review = True
        result_rows.append(out)
    return GridResult(rows=result_rows, tick_rows=len(rows), column_counts=counts)


def remove_rules(binary, vertical, scale, reference):
    ink = binary.copy()
    horizontal = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, int(45 * scale)), 1)),
    )
    ink[cv2.dilate(horizontal, np.ones((3, 1), np.uint8)) > 0] = 0
    height = ink.shape[0]
    for line in vertical:
        for y in range(height):
            x = int(round(line.at(y, reference)))
            ink[y, max(0, x - 3):x + 4] = 0
    return cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))


def find_tick_blobs(ink, vertical, top, reference, scale, keep_print=False):
    count, _, stats, centroids = cv2.connectedComponentsWithStats(ink)
    x_left = vertical[0].at(top, reference)
    x_right = vertical[-1].at(top, reference)
    column_width = (x_right - x_left) / (len(vertical) - 1)
    blobs = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        cx, cy = centroids[index]
        if cy < top + 4 * scale or not x_left < cx < x_right:
            continue
        # ticks: compact, at least ~10px tall; ruled-line leftovers are thin, long scraps
        if area < 70 * scale * scale or h < 10 * scale or w < 10 * scale or w > 0.95 * column_width:
            continue
        if not keep_print and (area > 700 * scale * scale or h > 45 * scale):
            continue  # frame fragments, photo edges, UI overlays
        edges = [line.at(cy, reference) for line in vertical]
        column = int(np.searchsorted(edges, cx, side="right")) - 1
        if not 0 <= column < len(vertical) - 1:
            continue
        margin = min(cx - edges[column], edges[column + 1] - cx) / column_width
        fill = area / float(w * h)
        blobs.append({
            "cx": float(cx), "cy": float(cy), "column": column, "area": area, "width": w,
            "solid": fill > 0.5 and area > 140 * scale * scale,
            "borderline": margin < 0.12,
        })
    return blobs


def group_rows(blobs, step):
    rows = []
    for blob in sorted(blobs, key=lambda item: item.get("ry", item["cy"])):
        if rows and blob.get("ry", blob["cy"]) - np.mean([b.get("ry", b["cy"]) for b in rows[-1]]) < 0.55 * step:
            rows[-1].append(blob)
        else:
            rows.append([blob])
    return rows


def looks_like_totals(row, blobs):
    median_area = float(np.median([b["area"] for b in blobs]))
    return max(b["area"] for b in row) > 2.0 * median_area


def find_row_step(segments, vertical, width, height, scale):
    reference = width * 0.6
    first = vertical[0].position
    span = vertical[-1].position - first
    lines = []
    for x1, y1, x2, y2 in segments:
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) < width * 0.12 or abs(dy) > abs(dx) * 0.08:
            continue
        if max(x1, x2) < first + span * 0.1 or min(x1, x2) > first + span * 0.6:
            continue
        y = y1 + dy * (reference - x1) / dx
        if height * 0.15 < y < height * 0.92:
            lines.append(GridLine(y, dy / dx))
    clusters = cluster_lines(lines, 4 * scale)
    positions = [line.position for line in clusters]
    gaps = [b - a for a, b in zip(positions, positions[1:]) if 20 * scale < b - a < 45 * scale]
    if not gaps:
        return None, 0.0
    return float(np.median(gaps)), float(np.median([line.slope for line in clusters]))


def find_body_top(ink, vertical, reference, height, scale, step):
    """The printed subject abbreviations (ISO, IMO, ...) form one row that has ink in almost
    every column. The body starts just below the lowest such row in the upper part of the page."""
    blobs = find_tick_blobs(ink, vertical, 0, reference, scale, keep_print=True)
    best = None
    for row in group_rows([b for b in blobs if b["cy"] < height * 0.4], step):
        if len({b["column"] for b in row}) >= 7:
            best = max(b["cy"] for b in row) if best is None else max(best, max(b["cy"] for b in row))
    return None if best is None else best + 0.45 * step


def find_vertical_lines(segments, width, height, column_count, scale):
    reference = height / 2
    candidates = []
    for x1, y1, x2, y2 in segments:
        dx, dy = x2 - x1, y2 - y1
        if abs(dy) < 180 * scale or abs(dx) > abs(dy) * 0.12:
            continue
        position = x1 + dx * (reference - y1) / dy
        if width * 0.2 < position < width * 0.97:
            candidates.append(GridLine(position, dx / dy))
    groups = cluster_lines(candidates, 9 * scale)
    if len(groups) < column_count + 1:
        return None
    choices = []
    for start, first in enumerate(groups):
        for last in groups[start + column_count:]:
            gap = (last.position - first.position) / column_count
            if not width * 0.025 < gap < width * 0.1:
                continue
            run, distances = [], []
            for index in range(column_count + 1):
                expected = first.position + index * gap
                nearest = min(groups, key=lambda line: abs(line.position - expected))
                distance = abs(nearest.position - expected)
                if distance > 18 * scale or nearest in run:
                    break
                run.append(nearest)
                distances.append(distance)
            if len(run) == column_count + 1:
                gaps = np.diff([line.position for line in run])
                if max(gaps) - min(gaps) < gap * 0.35:
                    choices.append((float(np.std(gaps) + np.mean(distances)), run))
    return min(choices, key=lambda choice: choice[0])[1] if choices else None


def cluster_lines(lines, tolerance):
    groups = []
    for line in sorted(lines, key=lambda item: item.position):
        if groups and abs(line.position - np.median([item.position for item in groups[-1]])) < tolerance:
            groups[-1].append(line)
        else:
            groups.append([line])
    return [
        GridLine(float(np.median([item.position for item in group])),
                 float(np.median([item.slope for item in group])))
        for group in groups
    ]