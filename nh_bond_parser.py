"""Parse NH layout text without treating vertically merged amounts as tenors.

Only validated events leave this module. Diagnostics also describe rejected rows,
so a partially readable table cannot be used as a complete history snapshot.
"""

import math
import re
from datetime import date


RATING_GRADE = r"(?:AAA|AA[+\-0]?|A[+\-0]?|BBB[+\-0]?|BB[+\-0]?|B[+\-0]?)(?:\([SP]\))?"
RATING = rf"{RATING_GRADE}(?:\s*/\s*{RATING_GRADE})*"
ISSUER_ROW = re.compile(rf"^\s*(?P<issuer>\S(?:.*?\S)?)\s{{2,}}(?P<rating>{RATING})(?=\s|$)")
TERM = r"(?:\d+(?:\.\d+)?(?:NC\d+(?:\.\d+)?)?년?|영구)"
TERMS = rf"{TERM}(?:\s*/\s*{TERM})*"
AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:억(?:원)?)?"
UNKNOWN = r"금액\s*미정|미정"
MANAGER = re.compile(r"(?<![0-9A-Za-z가-힣])(?:NH|KB|DB|한투|신한|미래|키움|삼성|하나|우리|교보|한양|대신|현대차|SK)(?:/(?:NH|KB|DB|한투|신한|미래|키움|삼성|하나|우리|교보|한양|대신|현대차|SK))*(?=\s|$)")
SCHEDULE = re.compile(r"(?P<demand>\d{1,2}/\d{1,2}(?:\([^)]+\))?|미정)\s+(?P<payment>\d{1,2}/\d{1,2}(?:\([^)]+\))?)")
BAND = re.compile(r"(?:(개별(?:민평)?|등급(?:민평)?)\s*)?(-?\d+(?:\.\d+)?)\s*~\s*(\+?\d+(?:\.\d+)?)")


class NHLayoutText(str):
    """Layout text with cell evidence extracted from the same PDF page."""

    def __new__(cls, text, cell_overrides=None):
        value = super().__new__(cls, text)
        value.cell_overrides = cell_overrides or {}
        return value


def valid_term(value):
    value = str(value or "").strip().removesuffix("년")
    if value == "영구":
        return True
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:NC(\d+(?:\.\d+)?))?", value)
    if not match:
        return False
    # Includes short fractional tenors, 30NC5 hybrids and 100-year bonds.
    maturity = float(match[1])
    return 0 < maturity <= 100 and (not match[2] or 0 < float(match[2]) <= maturity)


def positive_amount(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validation_errors(event):
    """Also validate legacy/injected NH events before display or history use."""
    if not isinstance(event, dict):
        return ["invalid event schema"]
    errors = list(event.get("validation_errors") or [])
    tranches = event.get("tranches") or []
    if not isinstance(tranches, list) or any(not isinstance(t, dict) for t in tranches):
        return errors + ["invalid tranche schema"]
    if not tranches or any(not valid_term(t.get("term")) for t in tranches):
        errors.append("invalid or missing tenor")
    terms = [str(t.get("term", "")).removesuffix("년") for t in tranches]
    display_terms = [t.strip().removesuffix("년") for t in str(event.get("term") or "").split("/")]
    if terms != display_terms:
        errors.append("tenor/tranche mismatch")
    amounts = [t.get("amount_eok") for t in tranches]
    known = [a for a in amounts if a is not None]
    valid_amounts = all(positive_amount(a) for a in known)
    if not valid_amounts:
        errors.append("invalid tranche amount")
    total = event.get("amount_eok")
    if total is not None and not positive_amount(total):
        errors.append("invalid total amount")
    if any(a is None for a in amounts):
        if event.get("amount_basis") == "shared_total" and positive_amount(total):
            if valid_amounts and sum(known) > total:
                errors.append("tranche sum exceeds total")
        elif not (event.get("amount_basis") == "explicitly_unknown" and total is None and not known):
            errors.append("missing tranche amount without explicit total")
    elif valid_amounts and known and (not positive_amount(total) or not math.isclose(sum(known), total, abs_tol=0.001)):
        errors.append("tranche sum/total mismatch")
    reported = event.get("reported_total_eok")
    if reported is not None and (not positive_amount(reported) or total != reported):
        errors.append("reported total mismatch")
    maximum = event.get("max_amount_eok")
    if maximum is not None and (not positive_amount(maximum) or (positive_amount(total) and maximum < total)):
        errors.append("maximum below total or invalid")
    issued = event.get("issued_tranches")
    if issued is not None:
        if not isinstance(issued, list) or any(not isinstance(t, dict) for t in issued):
            errors.append("invalid issued tranche schema")
        else:
            issued_terms = [str(t.get("term", "")).removesuffix("년") for t in issued]
            issued_amounts = [t.get("amount_eok") for t in issued]
            if issued_terms != terms or any(a is not None and not positive_amount(a) for a in issued_amounts):
                errors.append("invalid issued tranche conditions")
            known_issued = [a for a in issued_amounts if positive_amount(a)]
            if positive_amount(maximum) and sum(known_issued) > maximum:
                errors.append("known issued amount exceeds maximum")
            if issued_amounts and all(positive_amount(a) for a in issued_amounts):
                issued_total = event.get("issued_amount_eok")
                if not positive_amount(issued_total) or not math.isclose(sum(issued_amounts), issued_total, abs_tol=0.001):
                    errors.append("issued tranche sum/total mismatch")
                elif positive_amount(maximum) and issued_total > maximum:
                    errors.append("issued total exceeds maximum")
                elif event.get("no_increase") and positive_amount(total) and issued_total != total:
                    errors.append("issued total conflicts with no increase")
            elif event.get("issued_amount_eok") is not None:
                errors.append("incomplete issued allocation with total")
    demand, payment = event.get("demand_date"), event.get("payment_date")
    if not isinstance(payment, date) or (demand is not None and (not isinstance(demand, date) or demand > payment)):
        errors.append("invalid schedule")
    return list(dict.fromkeys(errors))


def _amount(cell):
    cell = cell.strip()
    if re.fullmatch(UNKNOWN, cell):
        return None, True
    if not re.fullmatch(AMOUNT, cell):
        raise ValueError("unreadable amount cell")
    return float(re.sub(r"억(?:원)?$", "", cell).replace(",", "")), False


def _columns(line):
    """Header centers define cell boundaries, independent of indentation size."""
    labels = list(re.finditer(r"대표\s*주관|주관사|발행금액|금액|합계|총액|최대(?:금액)?|Max|만기|등급", line, re.I))
    names = {"만기": "term", "금액": "amount", "발행금액": "amount", "합계": "total", "총액": "total", "등급": "rating"}
    centers = []
    for match in labels:
        label = match[0]
        name = names.get(label, "max" if label.lower() == "max" or label.startswith("최대") else "manager")
        centers.append((name, (match.start() + match.end()) / 2))
    if not {"rating", "term", "amount", "manager"}.issubset({name for name, _ in centers}):
        return None
    if len({name for name, _ in centers}) != len(centers):
        return None
    return {
        name: (int((centers[i - 1][1] + center) / 2) if i else 0,
               int((center + centers[i + 1][1]) / 2) if i + 1 < len(centers) else len(line))
        for i, (name, center) in enumerate(centers)
        if name in {"term", "amount", "total", "max"}
    }


def _wide_columns(line, following):
    """NH's multi-row header separates predicted/issued/underwritten amounts."""
    labels = {name: re.search(label, line) for name, label in {
        "term": r"만기", "amount": r"예측(?!결과)", "esg": r"ESG",
        "issued": r"발행", "underwriting": r"당사인수", "max": r"증액가능",
    }.items()}
    if not all(labels.values()):
        return None
    positions = [labels[name].start() for name in ("term", "amount", "esg", "issued", "underwriting", "max")]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        return None
    issuer_header = next((row for row in following if "발행회사" in row and "신용등급" in row), "")
    units = next((row for row in following if "(Y)" in row and row.count("금액(억)") >= 4), "")
    rating = re.search(r"신용등급", issuer_header)
    manager = re.search(r"대표\s*주관", issuer_header)
    amounts = list(re.finditer(r"금액\(억\)", units))
    tenor = re.search(r"\(Y\)", units)
    esg = re.search(r"여부", units)
    if not (rating and manager and tenor and esg and len(amounts) >= 4):
        return None
    if not amounts[0].end() < esg.start() < amounts[1].start():
        return None
    # The gaps between unit labels accommodate right-aligned numeric cells.
    return {
        "term": ((rating.start() + rating.end() + labels["term"].start() + labels["term"].end()) // 4,
                 (tenor.end() + amounts[0].start()) // 2),
        "amount": ((tenor.end() + amounts[0].start()) // 2,
                   (amounts[0].end() + esg.start()) // 2),
        "issued_amount": ((esg.end() + amounts[1].start()) // 2,
                          (amounts[1].end() + amounts[2].start()) // 2),
        "max": ((amounts[2].end() + amounts[3].start()) // 2,
                (amounts[3].end() + manager.start()) // 2),
    }


def extract_pdf_cell_overrides(page, layout):
    """Use PDF coordinates for columns and explicit clipping boxes for totals.

    The text rows must match every physical tenor row in order. A shared amount
    additionally requires a planned-amount cell spanning the entire issuer's
    tenor group. Plain text alone cannot establish that a blank is a merge.
    """
    records, stack = [], []
    state = {"clip": None, "path": None}
    last_show = None

    def point(x, y, matrix):
        return (x * matrix[0] + y * matrix[2] + matrix[4],
                x * matrix[1] + y * matrix[3] + matrix[5])

    def before(operator, operands, cm, tm):
        nonlocal last_show, state
        if operator == b"q":
            stack.append(dict(state))
        elif operator == b"Q":
            state = stack.pop() if stack else {"clip": None, "path": None}
        elif operator == b"re":
            x, y, width, height = map(float, operands)
            # Only axis-aligned cell boxes are usable evidence.
            if abs(cm[1]) < 0.001 and abs(cm[2]) < 0.001:
                x1, y1 = point(x, y, cm)
                x2, y2 = point(x + width, y + height, cm)
                state["path"] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            else:
                state["path"] = None
        elif operator in {b"W", b"W*"}:
            state["clip"] = state["path"]
        elif operator in {b"m", b"l", b"c", b"v", b"y", b"n", b"S", b"s", b"f", b"f*"}:
            state["path"] = None
        elif operator in {b"Tj", b"TJ"}:
            horizontal = (tm[0] * cm[0] + tm[1] * cm[2], tm[0] * cm[1] + tm[1] * cm[3])
            vertical = (tm[2] * cm[0] + tm[3] * cm[2], tm[2] * cm[1] + tm[3] * cm[3])
            if (abs(horizontal[1]) < 0.001 and abs(vertical[0]) < 0.001
                    and horizontal[0] > 0 and vertical[1] > 0):
                last_show = (*point(tm[4], tm[5], cm), state["clip"], horizontal[0], vertical[1])
            else:
                last_show = None

    def visit(text, cm, tm, font, size):
        # pypdf may reset tm before flushing a text object. Its last show
        # operation retains the coordinates of the decoded cell text.
        text = text.strip()
        if text and last_show and "\n" not in text and "\r" not in text:
            x, y, clip, xscale, yscale = last_show
            width = size * xscale * sum(0.5 if ord(char) < 128 else 1 for char in text)
            records.append({"text": text, "x": x, "y": y,
                            "center": x + width / 2, "size": size * yscale, "clip": clip})

    page.extract_text(visitor_operand_before=before, visitor_text=visit)
    headers = {name: next((r for r in records if r["text"] == label), None)
               for name, label in {"term": "만기", "amount": "예측", "esg": "ESG",
                                   "issued": "발행", "underwriting": "당사인수", "max": "증액가능"}.items()}
    if not all(headers.values()):
        return {}
    ordered = [headers[name] for name in ("term", "amount", "esg", "issued", "underwriting", "max")]
    if (any(abs(r["y"] - ordered[0]["y"]) > ordered[0]["size"] for r in ordered)
            or [r["center"] for r in ordered] != sorted(r["center"] for r in ordered)):
        return {}
    middle = lambda left, right: (headers[left]["center"] + headers[right]["center"]) / 2
    ranges = {
        "term": (headers["term"]["center"] - (middle("term", "amount") - headers["term"]["center"]), middle("term", "amount")),
        "amount": (middle("term", "amount"), middle("amount", "esg")),
        "issued_amount": (middle("esg", "issued"), middle("issued", "underwriting")),
        "max": (middle("underwriting", "max"), float("inf")),
    }
    manager = next((r for r in records if re.fullmatch(r"대표\s*주관", r["text"])), None)
    if not manager or manager["center"] <= headers["max"]["center"]:
        return {}
    ranges["max"] = (ranges["max"][0], (headers["max"]["center"] + manager["center"]) / 2)
    physical = [r for r in records if ranges["term"][0] < r["center"] < ranges["term"][1]
                and r["y"] < headers["term"]["y"] - 2 * r["size"]
                and re.fullmatch(TERMS, r["text"])
                and all(valid_term(t) for t in r["text"].split("/"))]
    physical.sort(key=lambda r: -r["y"])
    raw_rows, columns, issuer_start = [], None, None
    lines = layout.split("\n")
    for index, raw in enumerate(lines):
        line = raw.expandtabs(8).rstrip()
        header = _wide_columns(line, [s.expandtabs(8) for s in lines[index + 1:index + 4]])
        if header:
            columns = header
            continue
        if not columns:
            continue
        first = ISSUER_ROW.match(line)
        if first:
            issuer_start = index
        start, end = columns["term"]
        # Composite ratings can extend past the centered header boundary.
        # Their suffix is not part of the adjacent tenor cell.
        term = line[max(start, first.end() if first else start):end].strip()
        if issuer_start is not None and re.fullmatch(TERMS, term):
            raw_rows.append({"index": index, "line": line, "term": term, "issuer_start": issuer_start})
    if len(raw_rows) != len(physical) or not raw_rows:
        return {}
    if any(row["term"] != record["text"] for row, record in zip(raw_rows, physical)):
        return {}
    overrides = {}
    for row, tenor in zip(raw_rows, physical):
        baseline = [r for r in records if abs(r["y"] - tenor["y"]) < min(r["size"], tenor["size"]) * 0.4]
        cells = {"term": tenor["text"]}
        for name in ("amount", "issued_amount", "max"):
            start, end = ranges[name]
            selected = [r for r in baseline if start < r["center"] < end]
            if name == "max":
                # Managers can be left-aligned within the broad header cell.
                manager_start = min((r["x"] for r in baseline if MANAGER.fullmatch(r["text"])), default=end)
                selected = [r for r in selected if r["x"] < manager_start]
            cells[name] = " ".join(r["text"] for r in sorted(selected, key=lambda r: r["x"]))
        overrides[row["index"]] = {"line": row["line"], "cells": cells}
    for record in records:
        clip = record["clip"]
        if not clip or not re.fullmatch(AMOUNT, record["text"]):
            continue
        left, bottom, right, top = clip
        if not (left < headers["amount"]["center"] < right < headers["esg"]["center"]):
            continue
        indices = [i for i, r in enumerate(physical) if bottom < r["y"] < top]
        if len(indices) < 2:
            continue
        group = [row for row in raw_rows if row["issuer_start"] == raw_rows[indices[0]]["issuer_start"]]
        if [raw_rows[i]["index"] for i in indices] != [row["index"] for row in group]:
            continue
        values = [r for r in records if r["clip"] == clip and re.fullmatch(AMOUNT, r["text"])]
        if len(values) != 1:
            continue
        for row in group:
            overrides[row["index"]]["cells"]["amount"] = ""
        overrides[group[0]["index"]]["cells"]["total"] = record["text"]
    return overrides


def _month_day(value, reference_date):
    match = re.search(r"(\d{1,2})/(\d{1,2})", value)
    if not match:
        return None
    candidates = []
    for year in (reference_date.year - 1, reference_date.year, reference_date.year + 1):
        try:
            candidates.append(date(year, int(match[1]), int(match[2])))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("invalid month/day")
    return min(candidates, key=lambda value: abs((value - reference_date).days))


def _metadata_area(line):
    matches = [m for m in (MANAGER.search(line), BAND.search(line), SCHEDULE.search(line), re.search(r"고정|\d{1,2}/\d{1,2}(?:\([^)]+\))?", line)) if m]
    return line[:min(m.start() for m in matches)] if matches else line


def _add_terms(row, cell, amount_cell):
    if not re.fullmatch(TERMS, cell):
        row["errors"].append("unreadable tenor cell")
        return
    terms = [t.strip().removesuffix("년") for t in cell.split("/")]
    amount, unknown = _amount(amount_cell) if amount_cell else (None, False)
    if len(terms) > 1 and amount is not None:
        _set_total(row, amount)
        amount = None
    for term in terms:
        row["tranches"].append({"term": term, "amount_eok": amount})
        row["issued_amounts"].append(None)
        row["unknown"].append(unknown)


def _set_total(row, value):
    if row["total"] is not None and row["total"] != value:
        row["errors"].append("conflicting total cells")
    row["total"] = value


def _parse_cells(row, line, columns, first, override=None):
    if columns:
        cells = dict(override) if override else {name: line[start:end].strip() for name, (start, end) in columns.items()}
        issuer = ISSUER_ROW.match(line) if first else None
        if issuer and not override:
            start, end = columns["term"]
            cells["term"] = line[max(start, issuer.end()):end].strip()
        if "issued_amount" in columns and not override:
            # A left-aligned manager can start before its centered header.
            start, end = columns["max"]
            manager = next((m for m in MANAGER.finditer(line) if m.start() > start), None)
            cells["max"] = line[start:min(end, manager.start()) if manager else end].strip()
        before = len(row["tranches"])
        if cells["term"]:
            _add_terms(row, cells["term"], cells["amount"])
        elif cells["amount"]:
            # A wrapped amount is usable only in its actual amount column.
            if not row["tranches"] or row["tranches"][-1]["amount_eok"] is not None:
                row["errors"].append("orphan amount cell")
            else:
                amount, unknown = _amount(cells["amount"])
                row["tranches"][-1]["amount_eok"] = amount
                row["unknown"][-1] = unknown
        issued = cells.get("issued_amount")
        if issued and issued != "-":
            value, _ = _amount(issued)
            if len(row["tranches"]) - before == 1:
                row["issued_amounts"][-1] = value
            else:
                row["errors"].append("ambiguous issued amount cell")
        if cells.get("total"):
            total, unknown = _amount(cells["total"])
            if unknown:
                row["errors"].append("unreadable total cell")
            else:
                _set_total(row, total)
        if cells.get("max"):
            if re.fullmatch(r"증액\s*없음", cells["max"]):
                row["no_increase"] = True
            else:
                maximum, _ = _amount(cells["max"])
                if row["max"] is not None and row["max"] != maximum:
                    row["errors"].append("conflicting maximum cells")
                row["max"] = maximum
        return

    # Compact text has no geometry. Require a tenor AND an adjacent amount;
    # bare numbers and missing continuation amounts are never silently ignored.
    area = _metadata_area(line).strip()
    if not area:
        return
    match = re.fullmatch(rf"(?P<terms>{TERMS})(?P<gap>\s+)(?P<amount>{AMOUNT}|{UNKNOWN})(?P<extra>.*)", area)
    if not match:
        if re.fullmatch(TERMS, area):
            _add_terms(row, area, "")
        else:
            row["errors"].append("incomplete or ambiguous continuation row")
        return
    if len(match["gap"]) > 20:
        row["errors"].append("amount outside inferred column")
        _add_terms(row, match["terms"], "")
        return
    _add_terms(row, match["terms"], match["amount"])
    extra = match["extra"].strip()
    if not extra:
        return
    no_increase = bool(re.search(r"증액\s*없음", extra))
    extra = re.sub(r"증액\s*없음", "", extra).strip()
    if no_increase:
        extra = extra.strip("() ")
    explicit_max = re.fullmatch(rf"\(?\s*(?:Max|최대)\s*:?\s*({AMOUNT})\s*\)?", extra, re.I)
    if explicit_max:
        row["max"], _ = _amount(explicit_max[1])
    else:
        values = re.split(r"\s+", extra) if extra else []
        if not first or len(values) > 2:
            row["errors"].append("ambiguous extra amount cells")
        elif len(values) == 2:
            total, _ = _amount(values[0])
            _set_total(row, total)
            row["max"], _ = _amount(values[1])
        elif values:
            value, _ = _amount(values[0])
            if no_increase:
                _set_total(row, value)
            else:
                row["max"] = value
    row["no_increase"] |= no_increase


def parse_schedule(text, reference_date, pdf_url, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else []
    rows, current, columns = [], None, None
    unsafe_layout = False
    raw_lines = (text or "").split("\n")
    overrides = text.cell_overrides if isinstance(text, NHLayoutText) else {}
    for index, raw_line in enumerate(raw_lines):
        if "\f" in raw_line:
            columns = None
            raw_line = raw_line.replace("\f", "")
        line = raw_line.expandtabs(8).rstrip()
        header = _wide_columns(line, [s.expandtabs(8) for s in raw_lines[index + 1:index + 4]]) or _columns(line)
        if header:
            columns = header
            unsafe_layout = False
            # Repeated headers may separate an issuer's tranches across pages.
            continue
        if all(word in line for word in ("만기", "예측", "당사인수", "증액가능")):
            columns = None
            unsafe_layout = True
            continue
        if (re.match(r"\s*20\d{2}[-/]\d{1,2}[-/]\d{1,2}", line) and "List" in line) or "당사 대표주관" in line or "당사 인수" in line:
            continue
        if ("발행회사" in line and "신용등급" in line) or ("(Y)" in line and line.count("금액(억)") >= 4):
            continue
        first = ISSUER_ROW.match(line)
        if first:
            current = {
                "issuer": " ".join(first["issuer"].split()),
                "rating": first["rating"],
                "tranches": [],
                "issued_amounts": [],
                "unknown": [],
                "total": None,
                "max": None,
                "no_increase": False,
                "errors": ["unrecognized multi-row header"] if unsafe_layout else [],
                "lines": [],
            }
            rows.append(current)
        elif re.search(rf"(?<!\S){RATING}(?!\S)", line) and (SCHEDULE.search(line) or MANAGER.search(line)):
            diagnostics.append({"issuer": "unknown", "errors": ["unrecognized issuer row"]})
            current = None
            continue
        elif not line.strip():
            continue
        elif re.fullmatch(r"[-/|]+", line.strip()):
            continue
        elif not current:
            if re.search(r"\d{1,2}/\d{1,2}\([^)]+\)", line) and re.search(RATING, line):
                diagnostics.append({"issuer": "unknown", "errors": ["unrecognized issuer row"]})
            continue
        elif columns and re.fullmatch(r"\([^)]*\)", line[:columns["term"][0]].strip()):
            # Collateral/guarantee descriptions occupy the issuer column while
            # the same physical row carries a continuation tenor and amount.
            pass
        elif columns and line[:columns["term"][0]].strip():
            # An unreadable issuer/rating still ends the previous issuer.
            # Its metadata and subsequent tenors must never attach upstream.
            diagnostics.append({"issuer": "unknown", "errors": ["unrecognized issuer row"]})
            current = None
            continue
        elif not columns and re.match(r"^\s*\([^)]*\)\s+\d", line):
            current["errors"].append("description continuation needs column header")
            continue
        elif not re.match(r"^\s+(?:\d|영구|금액|미정|증액)", line):
            # Metadata can wrap independently of the numeric columns.
            if MANAGER.search(line) or BAND.search(line) or SCHEDULE.search(line):
                current["lines"].append(line)
                continue
            current = None
            continue
        current["lines"].append(line[first.end():] if first else line)
        try:
            numeric_line = line[first.end():] if first and not columns else line
            evidence = overrides.get(index, {})
            override = evidence.get("cells") if evidence.get("line") == line and not unsafe_layout else None
            _parse_cells(current, numeric_line, columns, bool(first), override)
        except ValueError as error:
            current["errors"].append(str(error))

    if not rows and not re.search(r"(?:발행예정|예정물)\s*없음", text or ""):
        diagnostics.append({"issuer": "unknown", "errors": ["unrecognized NH schedule table"]})
    events = []
    for row in rows:
        metadata = " ".join(row["lines"])
        schedule = SCHEDULE.search(metadata)
        manager = MANAGER.search(metadata)
        band = BAND.search(metadata)
        if not schedule:
            row["errors"].append("missing schedule")
        try:
            demand = _month_day(schedule["demand"], reference_date) if schedule else None
            payment = _month_day(schedule["payment"], reference_date) if schedule else None
        except ValueError:
            demand, payment = None, None
            row["errors"].append("invalid date")
        tranches = row["tranches"]
        known = [t["amount_eok"] for t in tranches if t["amount_eok"] is not None]
        complete = bool(tranches) and len(known) == len(tranches)
        total = sum(known) if complete else row["total"]
        if complete:
            basis = "tranche_sum"
        elif total is not None:
            basis = "shared_total"
        elif row["unknown"] and all(row["unknown"]):
            basis = "explicitly_unknown"
        else:
            basis = "incomplete"
        terms = [t["term"] for t in tranches]
        term_text = "/".join(terms)
        if terms and all(re.fullmatch(r"\d+(?:\.\d+)?", t) for t in terms):
            term_text += "년"
        if band:
            rate_band = (f"{band[1]} " if band[1] else "") + f"{band[2]}~{band[3]}bp"
        else:
            rate_band = "고정" if "고정" in metadata else None
        event = {
            "source": "nh_pdf",
            "issuer": row["issuer"],
            "rating": row["rating"],
            "term": term_text,
            "tranches": tranches,
            "amount_eok": total,
            "amount_basis": basis,
            "reported_total_eok": row["total"],
            "max_amount_eok": None if row["no_increase"] else row["max"],
            "no_increase": row["no_increase"],
            "managers": manager[0].split("/") if manager else [],
            "security_type": None,
            "demand_date": demand,
            "demand_date_text": schedule["demand"] if schedule else None,
            "payment_date": payment,
            "start_time": None,
            "end_time": None,
            "rate_band": rate_band,
            "report_url": pdf_url,
            "validation_errors": row["errors"],
        }
        if "issued_amounts" in row and any(a is not None for a in row["issued_amounts"]):
            event["issued_tranches"] = [
                {"term": t["term"], "amount_eok": amount}
                for t, amount in zip(tranches, row["issued_amounts"])
            ]
            event["issued_amount_eok"] = (
                sum(row["issued_amounts"])
                if all(positive_amount(a) for a in row["issued_amounts"]) else None
            )
        errors = validation_errors(event)
        if errors:
            diagnostics.append({"issuer": row["issuer"], "errors": errors})
        else:
            events.append(event)
    return events
