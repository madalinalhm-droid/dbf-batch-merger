# -*- coding: utf-8 -*-
"""Minimal, dependency-free reader/writer for dBASE / FoxPro .dbf tables.

Records are kept as raw bytes and copied unchanged, so text in legacy code
pages (cp852, cp1250, ...) and national characters survive exactly as they
were in the source file. Memo fields are not supported.
"""
import datetime
import struct

__all__ = ["Dbf", "DbfError", "create_dbf", "make_mapper", "blank", "convert"]


class DbfError(Exception):
    """Raised for unreadable or unsupported tables."""


# Field types stored as binary data (not padded with spaces).
BIN_TYPES = set("IBYT0G")


class Dbf:
    """A .dbf table loaded fully into memory."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 33:
            raise DbfError("Invalid DBF file: %s" % path)
        self.nrec = struct.unpack("<I", data[4:8])[0]
        self.hlen, self.rlen = struct.unpack("<HH", data[8:12])
        self.header = bytearray(data[: self.hlen])
        self.fields = []
        pos, offset = 32, 1  # offset 0 is the deletion flag
        while pos + 32 <= self.hlen and data[pos] != 0x0D:
            d = data[pos: pos + 32]
            name = d[:11].split(b"\x00")[0].decode("ascii", "replace").strip().upper()
            self.fields.append({"name": name, "type": chr(d[11]).upper(),
                                "len": d[16], "dec": d[17], "off": offset})
            offset += d[16]
            pos += 32
        if offset != self.rlen:
            raise DbfError("Unexpected DBF structure: %s" % path)
        for fl in self.fields:
            if fl["type"] in ("M", "G", "W"):
                raise DbfError("Memo fields are not supported (%s): %s" % (fl["name"], path))
        self.records = []
        for i in range(self.nrec):
            start = self.hlen + i * self.rlen
            rec = data[start: start + self.rlen]
            if len(rec) < self.rlen:
                break
            self.records.append(bytes(rec))

    def field(self, name):
        """Field descriptor by name (case-insensitive) or None."""
        name = name.upper()
        for fl in self.fields:
            if fl["name"] == name:
                return fl
        return None

    def save(self, path, records):
        """Write `records` (raw bytes) to `path`, reusing this table's header."""
        h = bytearray(self.header)
        today = datetime.date.today()
        h[1], h[2], h[3] = today.year - 1900, today.month, today.day
        h[4:8] = struct.pack("<I", len(records))
        h[28] &= 0xFE  # drop the "structural .cdx index attached" flag
        with open(path, "wb") as f:
            f.write(h)
            for r in records:
                f.write(r)
            f.write(b"\x1a")


def blank(fl):
    """Empty value for a field."""
    if fl["type"] in BIN_TYPES:
        return b"\x00" * fl["len"]
    return b" " * fl["len"]


def convert(raw, sf, tf):
    """Adapt a source field value to the target field (like FoxPro APPEND FROM)."""
    if sf["type"] == tf["type"] and sf["len"] == tf["len"] and sf["dec"] == tf["dec"]:
        return raw
    tt, length = tf["type"], tf["len"]
    if tt == "C" and sf["type"] not in BIN_TYPES:
        txt = raw.strip() if sf["type"] in "NF" else raw.rstrip()
        return txt[:length].ljust(length, b" ")
    if tt in ("N", "F") and sf["type"] in ("N", "F", "C"):
        s = raw.strip()
        if not s:
            return b" " * length
        try:
            v = float(s.decode("ascii").replace(",", "."))
        except ValueError:
            return b" " * length
        out = ("%*.*f" % (length, tf["dec"], v)).encode("ascii")
        if len(out) > length:
            raise DbfError("Value %s does not fit in field %s" % (s.decode(), tf["name"]))
        return out
    if tt == "D" and sf["type"] in ("D", "C"):
        return raw.strip()[:8].ljust(8, b" ")
    if tt == "L" and sf["type"] == "L":
        return raw[:1]
    return blank(tf)


def make_mapper(target, source):
    """Return (mapper, missing_fields): mapper turns a source record into a
    record with the target structure, matching fields by name."""
    plan = [(tf, source.field(tf["name"])) for tf in target.fields]
    missing = [tf["name"] for tf, sf in plan if sf is None and tf["type"] != "0"]

    def mapper(rec):
        parts = [b" "]
        for tf, sf in plan:
            if sf is None:
                parts.append(blank(tf))
            else:
                parts.append(convert(rec[sf["off"]: sf["off"] + sf["len"]], sf, tf))
        return b"".join(parts)

    return mapper, missing


def create_dbf(path, fields, rows, encoding="cp1250", deleted=()):
    """Create a simple dBASE III table (used for sample data and tests).

    fields  -- list of (name, type, length, decimals), types C / N / D / L
    rows    -- list of dicts {field_name: value}
    deleted -- indexes of rows to mark as deleted
    """
    nfields = len(fields)
    hlen = 32 + 32 * nfields + 1
    rlen = 1 + sum(f[2] for f in fields)
    today = datetime.date.today()
    header = bytearray(32)
    header[0] = 0x03
    header[1], header[2], header[3] = today.year - 1900, today.month, today.day
    header[4:8] = struct.pack("<I", len(rows))
    header[8:12] = struct.pack("<HH", hlen, rlen)
    descr = bytearray()
    for name, ftype, length, dec in fields:
        d = bytearray(32)
        d[: len(name)] = name.upper().encode("ascii")
        d[11] = ord(ftype)
        d[16], d[17] = length, dec
        descr += d
    out = bytearray(header + descr + b"\x0d")
    for i, row in enumerate(rows):
        rec = bytearray(b"*" if i in deleted else b" ")
        for name, ftype, length, dec in fields:
            v = row.get(name)
            if v is None or v == "":
                rec += b" " * length
            elif ftype == "N":
                rec += ("%*.*f" % (length, dec, float(v))).encode("ascii")[:length]
            elif ftype == "D":
                rec += v.strftime("%Y%m%d").encode("ascii") if hasattr(v, "strftime") \
                    else str(v)[:8].encode("ascii")
            elif ftype == "L":
                rec += b"T" if v else b"F"
            else:
                rec += str(v).encode(encoding)[:length].ljust(length, b" ")
        out += rec
    out += b"\x1a"
    with open(path, "wb") as f:
        f.write(out)
