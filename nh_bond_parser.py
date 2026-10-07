"""Parse NH layout text without treating vertically merged amounts as tenors.

Only validated events leave this module. Diagnostics also describe rejected rows,
so a partially readable table cannot be used as a complete history snapshot.
"""

import math
import re
from datetime import date, timedelta


RATING = r"(?:AAA|AA[+\-0]?|A[+\-0]?|BBB[+\-0]?|BB[+\-0]?|B[+\-0]?)(?:\(P\))?"
ISSUER_ROW = re.compile(rf"^\s*(?P<issuer>\S(?:.*?\S)?)\s{{2,}}(?P<rating>{RATING})(?=\s|$)")
TERM = r"(?:\d+(?:\.\d+)?(?:NC\d+(?:\.\d+)?)?년?|영구)"
TERMS = rf"{TERM}(?:\s*/\s*{TERM})*"
AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:억(?:원)?)?"
UNKNOWN = r"금액\s*미정|미정"
MANAGER = re.compile(r"(?<![0-9A-Za-z가-힣])(?:NH|KB|한투|신한|미래|키움|삼성|하나|우리|교보|한양|대신|SK)(?:/(?:NH|KB|한투|신한|미래|키움|삼성|하나|우리|교보|한양|대신|SK))*(?=\s|$)")
SCHEDULE = re.compile(r"(?P<demand>\d{1,2}/\d{1,2}(?:\([^)]+\))?|미정)\s+(?P<payment>\d{1,2}/\d{1,2}(?:\([^)]+\))?)")
BAND = re.compile(r"(?:(개별(?:민평)?|등급(?:민평)?)\s*)?(-?\d+(?:\.\d+)?)\s*~\s*(\+?\d+(?:\.\d+)?)")


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


def _month_day(value, reference_date):
    match = re.search(r"(\d{1,2})/(\d{1,2})", value)
    if not match:
        return None
    result = reference_date.replace(month=int(match[1]), day=int(match[2]))
    if result < reference_date - timedelta(days=180):
        result = result.replace(year=result.year + 1)
    return result


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
        row["unknown"].append(unknown)


def _set_total(row, value):
    if row["total"] is not None and row["total"] != value:
        row["errors"].append("conflicting total cells")
    row["total"] = value


def _parse_cells(row, line, columns, first):
    if columns:
        cells = {name: line[start:end].strip() for name, (start, end) in columns.items()}
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
    for raw_line in (text or "").split("\n"):
        if "\f" in raw_line:
            columns = None
            raw_line = raw_line.replace("\f", "")
        line = raw_line.expandtabs(8).rstrip()
        header = _columns(line)
        if header:
            columns = header
            # Repeated headers may separate an issuer's tranches across pages.
            continue
        first = ISSUER_ROW.match(line)
        if first:
            current = {
                "issuer": " ".join(first["issuer"].split()),
                "rating": first["rating"],
                "tranches": [],
                "unknown": [],
                "total": None,
                "max": None,
                "no_increase": False,
                "errors": [],
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
            _parse_cells(current, numeric_line, columns, bool(first))
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
        errors = validation_errors(event)
        if errors:
            diagnostics.append({"issuer": row["issuer"], "errors": errors})
        else:
            events.append(event)
    return events
